"""Analysis agent: LLM JSON assessment with injected DATA (temp=0)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from llm_client import chat_completions

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

SCHEMA_TOP_KEYS = (
    "ticker",
    "as_of",
    "ratios",
    "assessment",
    "bull_case",
    "bear_case",
    "confidence",
    "sources_cited",
)


def _load(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def analyze(data: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """Produce assessment JSON. In dry_run, build a template from computed ratios only."""
    if dry_run:
        return _dry_assessment(data)

    system = _load("system.md")
    user_tmpl = _load("analysis_user.md")
    schema_json = _load("assessment_schema.json")
    user = (
        user_tmpl.replace("{{SCHEMA_JSON}}", schema_json)
        .replace("{{DATA_JSON}}", json.dumps(data, indent=2, default=str))
    )

    content = chat_completions(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
        max_tokens=2048,
    )
    raw = _parse_json_object(content)
    return normalize_assessment(raw, data=data)


def overall_health(assessment: dict[str, Any]) -> int | None:
    """Safe accessor for pipeline printing after normalize."""
    inner = assessment.get("assessment")
    if isinstance(inner, dict):
        h = inner.get("overall_health")
        if isinstance(h, bool):
            return None
        if isinstance(h, (int, float)):
            return int(h)
    # fall back if model left health at top level
    for key in ("overall_health", "health_score", "health"):
        h = assessment.get(key)
        if isinstance(h, (int, float)) and not isinstance(h, bool):
            return int(h)
    return None


def normalize_assessment(
    raw: dict[str, Any],
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Unwrap accidental nesting and map aliases onto assessment_schema.json."""
    obj = dict(raw) if isinstance(raw, dict) else {}
    obj = _unwrap_nested_assessment(obj)

    # Pull common aliases up / remap
    ratios = obj.get("ratios")
    if not isinstance(ratios, dict):
        km = obj.get("key_metrics")
        ratios = km if isinstance(km, dict) else {}
    if data and not ratios:
        ratios = dict(data.get("ratios") or {})
    # Keep only schema ratio fields when present; fill from DATA if missing
    data_ratios = (data or {}).get("ratios") or {}
    clean_ratios = {
        "pe": _num_or_none(ratios.get("pe", data_ratios.get("pe"))),
        "debt_to_equity": _num_or_none(
            ratios.get("debt_to_equity", data_ratios.get("debt_to_equity"))
        ),
        "current_ratio": _num_or_none(
            ratios.get("current_ratio", data_ratios.get("current_ratio"))
        ),
        "gross_margin": _num_or_none(
            ratios.get("gross_margin", data_ratios.get("gross_margin"))
        ),
    }

    inner = obj.get("assessment")
    if not isinstance(inner, dict):
        inner = {}
    else:
        inner = dict(inner)

    # If model stuffed narrative fields at top level, fold them in
    for field in ("growth", "profitability", "solvency"):
        if field not in inner and isinstance(obj.get(field), str):
            inner[field] = obj[field]

    # Map health aliases → overall_health
    health = _coerce_health(
        inner.get("overall_health"),
        inner.get("health_score"),
        inner.get("health"),
        obj.get("overall_health"),
        obj.get("health_score"),
        obj.get("health"),
    )
    if health is not None:
        inner["overall_health"] = health

    # Derive growth/profitability/solvency from chain_of_thought if missing
    cot = (
        inner.get("chain_of_thought")
        or obj.get("chain_of_thought")
        or ""
    )
    if isinstance(cot, str) and cot.strip():
        _fill_factor_texts_from_cot(inner, cot)

    # Ensure required string fields exist
    for field in ("growth", "profitability", "solvency"):
        val = inner.get(field)
        if not isinstance(val, str) or not val.strip():
            inner[field] = "data unavailable"

    # Drop non-schema junk from assessment object
    assessment_obj = {
        "growth": inner["growth"].strip(),
        "profitability": inner["profitability"].strip(),
        "solvency": inner["solvency"].strip(),
        "overall_health": int(inner.get("overall_health") or 3),
    }
    # clamp 1–5
    assessment_obj["overall_health"] = max(1, min(5, assessment_obj["overall_health"]))

    bull = obj.get("bull_case") or inner.get("bull_case") or ""
    bear = obj.get("bear_case") or inner.get("bear_case") or ""
    if not isinstance(bull, str):
        bull = str(bull)
    if not isinstance(bear, str):
        bear = str(bear)

    conf = obj.get("confidence") or inner.get("confidence") or "medium"
    if conf not in {"high", "medium", "low"}:
        conf = "medium"

    sources = obj.get("sources_cited") or inner.get("sources_cited")
    if not isinstance(sources, list):
        sources = list((data or {}).get("sources_cited") or [])
    sources = [str(s) for s in sources]

    ticker = obj.get("ticker") or (data or {}).get("ticker")
    as_of = obj.get("as_of") or (data or {}).get("as_of")

    return {
        "ticker": ticker,
        "as_of": as_of,
        "ratios": clean_ratios,
        "assessment": assessment_obj,
        "bull_case": bull.strip() or "data unavailable",
        "bear_case": bear.strip() or "data unavailable",
        "confidence": conf,
        "sources_cited": sources,
    }


def _unwrap_nested_assessment(obj: dict[str, Any]) -> dict[str, Any]:
    """If model returned {"assessment": {full schema-ish object}}, unwrap once/twice."""
    cur = obj
    for _ in range(3):
        if not isinstance(cur, dict):
            break
        # Classic double wrap: only key is assessment and nested looks like full payload
        nested = cur.get("assessment")
        if not isinstance(nested, dict):
            break
        looks_like_full = any(
            k in nested for k in ("bull_case", "bear_case", "confidence", "ratios", "ticker")
        )
        # Or nested itself wraps again
        deeper = nested.get("assessment") if isinstance(nested.get("assessment"), dict) else None
        if looks_like_full and not any(k in cur for k in ("bull_case", "bear_case") if k != "assessment"):
            # Prefer nested as the real object; keep outer crumbs only if nested lacks them
            merged = dict(nested)
            for k, v in cur.items():
                if k == "assessment":
                    continue
                if k not in merged:
                    merged[k] = v
            # If nested still has an assessment sub-object that is the factor block, keep it
            cur = merged
            continue
        if deeper is not None and any(
            k in deeper for k in ("growth", "profitability", "solvency", "overall_health", "health_score")
        ):
            # assessment.assessment.factor — lift factor block stays under assessment
            break
        break
    return cur


def _fill_factor_texts_from_cot(inner: dict[str, Any], cot: str) -> None:
    """Best-effort split of a numbered CoT into growth / profitability / solvency."""
    parts = re.split(r"(?=\d+\))", cot)
    parts = [p.strip() for p in parts if p.strip()]
    # Heuristic keyword buckets
    buckets = {"growth": [], "profitability": [], "solvency": []}
    for p in parts:
        low = p.lower()
        if any(w in low for w in ("revenue", "yoy", "growth", "top-line", "top line")):
            buckets["growth"].append(p)
        if any(w in low for w in ("margin", "profit", "income", "gross")):
            buckets["profitability"].append(p)
        if any(
            w in low
            for w in ("debt", "leverage", "current ratio", "liquidity", "solvency", "d/e")
        ):
            buckets["solvency"].append(p)
    for field, chunks in buckets.items():
        if (not isinstance(inner.get(field), str) or not str(inner.get(field)).strip()) and chunks:
            inner[field] = " ".join(chunks)
    # If still empty, stash whole CoT into growth as last resort (other fields filled later)
    if not isinstance(inner.get("growth"), str) or not inner["growth"].strip():
        if cot.strip():
            inner["growth"] = cot.strip()


def _coerce_health(*candidates: Any) -> int | None:
    for c in candidates:
        if c is None or isinstance(c, bool):
            continue
        if isinstance(c, (int, float)):
            return int(c)
        if isinstance(c, str):
            m = re.search(r"[1-5]", c)
            if m:
                return int(m.group(0))
    return None


def _num_or_none(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    # Strip accidental markdown fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Try to locate first {...} blob
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("Analysis response was not a JSON object")
    return obj


def _dry_assessment(data: dict[str, Any]) -> dict[str, Any]:
    ratios = data.get("ratios") or {}
    lines = data.get("lines") or {}
    yoy = lines.get("yoy_revenue_growth")
    growth = (
        f"YoY revenue growth ≈ {yoy:.2%}" if isinstance(yoy, float) else "YoY revenue: data unavailable"
    )
    gm = ratios.get("gross_margin")
    prof = (
        f"Gross margin ≈ {gm:.2%}" if isinstance(gm, float) else "Gross margin: data unavailable"
    )
    dte, cr = ratios.get("debt_to_equity"), ratios.get("current_ratio")
    solv_parts = []
    if isinstance(dte, float):
        solv_parts.append(f"D/E={dte:.2f}")
    if isinstance(cr, float):
        solv_parts.append(f"current ratio={cr:.2f}")
    solv = ", ".join(solv_parts) if solv_parts else "solvency ratios: data unavailable"

    return {
        "ticker": data.get("ticker"),
        "as_of": data.get("as_of"),
        "ratios": {
            "pe": ratios.get("pe"),
            "debt_to_equity": ratios.get("debt_to_equity"),
            "current_ratio": ratios.get("current_ratio"),
            "gross_margin": ratios.get("gross_margin"),
        },
        "assessment": {
            "growth": growth + " [dry-run: no LLM]",
            "profitability": prof + " [dry-run: no LLM]",
            "solvency": solv + " [dry-run: no LLM]",
            "overall_health": 3,
        },
        "bull_case": "Dry-run placeholder — run without --dry-run once HKU_GENAI_API_KEY is set.",
        "bear_case": "Dry-run placeholder — qualitative cases require the LLM step.",
        "confidence": "low",
        "sources_cited": data.get("sources_cited", []),
        "dry_run": True,
    }
