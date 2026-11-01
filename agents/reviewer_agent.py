"""Reviewer agent: flag invented numbers / missing citations."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from llm_client import chat_completions

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def review(
    data: dict[str, Any],
    assessment: dict[str, Any],
    report_md: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        return _heuristic_review(data, assessment, report_md)

    system = (PROMPTS / "system.md").read_text(encoding="utf-8")
    tmpl = (PROMPTS / "reviewer_user.md").read_text(encoding="utf-8")
    user = (
        tmpl.replace("{{DATA_JSON}}", json.dumps(data, indent=2, default=str))
        .replace("{{ASSESSMENT_JSON}}", json.dumps(assessment, indent=2, default=str))
        .replace("{{REPORT_MD}}", report_md)
    )
    content = chat_completions(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
        max_tokens=1500,
    )
    return json.loads(content)


def _collect_known_numbers(*objs: Any) -> set[str]:
    """Flatten numeric values to comparable string tokens."""
    known: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            known.add(f"{x:.6g}")
            known.add(f"{x:.4g}")
            known.add(f"{x:.2f}")
            known.add(str(x))
            if abs(x) < 10 and isinstance(x, float):
                known.add(f"{x:.2%}")
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    for o in objs:
        walk(o)
    return known


def _heuristic_review(
    data: dict[str, Any],
    assessment: dict[str, Any],
    report_md: str,
) -> dict[str, Any]:
    """Lightweight offline check: numbers in report should appear in DATA/assessment."""
    known = _collect_known_numbers(data, assessment)
    # Also accept "data unavailable" and health integers already in assessment
    candidates = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?%?(?![A-Za-z])", report_md)
    invented = []
    for tok in candidates:
        raw = tok.rstrip("%")
        ok = False
        for k in known:
            if raw in k or k in raw:
                ok = True
                break
            try:
                if abs(float(raw) - float(k.rstrip("%"))) < 1e-6:
                    ok = True
                    break
            except ValueError:
                pass
        # ignore lone small integers that are section noise / health score
        if not ok and raw not in {"1", "2", "3", "4", "5"}:
            invented.append(tok)

    # de-dupe preserving order
    seen = set()
    invented_u = []
    for x in invented:
        if x not in seen:
            seen.add(x)
            invented_u.append(x)

    missing = []
    if re.search(r"\d", report_md) and "[" not in report_md:
        missing.append("Report uses figures but has no [source] citations")

    return {
        "ok": len(invented_u) == 0,
        "invented_numbers": invented_u[:20],
        "missing_citations": missing,
        "contradictions": [],
        "suggestions": [
            "Dry-run used heuristic reviewer only; enable LLM reviewer for deeper checks."
        ],
        "dry_run": True,
    }
