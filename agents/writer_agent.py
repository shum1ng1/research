"""Writer agent: short markdown report from DATA + assessment."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from llm_client import chat_completions

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

WRITER_SYSTEM_EXTRA = (
    "You are in the WRITER role. Output Markdown prose only. "
    "Never return a JSON object. Never lead with a ```json code fence. "
    "Do not paste the ASSESSMENT blob verbatim."
)


def write_report(
    data: dict[str, Any],
    assessment: dict[str, Any],
    *,
    dry_run: bool = False,
) -> str:
    if dry_run:
        return _dry_report(data, assessment)

    system = (PROMPTS / "system.md").read_text(encoding="utf-8")
    system = system + "\n\n" + WRITER_SYSTEM_EXTRA
    tmpl = (PROMPTS / "writer_user.md").read_text(encoding="utf-8")
    user = (
        tmpl.replace("{{TICKER}}", str(data.get("ticker", "")))
        .replace("{{DATA_JSON}}", json.dumps(data, indent=2, default=str))
        .replace("{{ASSESSMENT_JSON}}", json.dumps(assessment, indent=2, default=str))
    )
    raw = chat_completions(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=2048,
    )
    return sanitize_report_markdown(raw, ticker=str(data.get("ticker") or "TICKER"))


def sanitize_report_markdown(text: str, *, ticker: str = "TICKER") -> str:
    """Strip leading JSON dumps / fences so the file is readable Markdown."""
    s = (text or "").strip()
    if not s:
        return f"# {ticker} — Equity research draft\n\n(data unavailable)\n"

    # Drop a leading ```json ... ``` block if present
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*", s, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        remainder = s[fence.end() :].strip()
        if remainder:
            s = remainder
        else:
            # Model only returned JSON — do not keep it as the report body
            s = ""

    # If the whole response is a bare JSON object, discard it
    if s.startswith("{") and '"assessment"' in s[:200]:
        try:
            json.loads(s)
            s = ""
        except json.JSONDecodeError:
            # Maybe JSON then markdown — peel JSON object from the start
            s = _strip_leading_json_object(s)

    s = s.strip()
    if not s:
        return (
            f"# {ticker} — Equity research draft\n\n"
            "_Writer returned JSON-only output; re-run after prompt fix._\n"
        )

    # Ensure we start with a heading
    if not s.lstrip().startswith("#"):
        s = f"# {ticker} — Equity research draft\n\n{s}"
    return s.strip() + "\n"


def _strip_leading_json_object(s: str) -> str:
    """If text starts with a JSON object, remove that object and keep the rest."""
    if not s.startswith("{"):
        return s
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                rest = s[i + 1 :].lstrip()
                # also drop a following fence close leftovers
                rest = re.sub(r"^```\s*", "", rest)
                return rest
    return s


def _dry_report(data: dict[str, Any], assessment: dict[str, Any]) -> str:
    t = data.get("ticker")
    price = (data.get("price") or {}).get("price")
    currency = (data.get("price") or {}).get("currency", "")
    ratios = assessment.get("ratios") or data.get("ratios") or {}
    a = assessment.get("assessment") or {}
    sources = ", ".join(assessment.get("sources_cited") or data.get("sources_cited") or [])

    def fmt(v: Any) -> str:
        if v is None:
            return "data unavailable"
        if isinstance(v, float):
            return f"{v:.4g}"
        return str(v)

    income_cite = next(
        (s for s in (data.get("sources_cited") or []) if "income" in s),
        "[income_statement]",
    )
    return f"""# {t} — Equity research draft (dry-run)

## Overview
Last price: {fmt(price)} {currency} (as of {data.get('as_of')}).  
Source: yfinance. This draft was generated **without** the LLM (`--dry-run`).

## Recent financials
- Revenue (latest annual): {fmt((data.get('lines') or {}).get('revenue'))} {income_cite}
- Net income: {fmt((data.get('lines') or {}).get('net_income'))}
- YoY revenue growth: {fmt((data.get('lines') or {}).get('yoy_revenue_growth'))}

## Ratios & health
| Ratio | Value |
|-------|-------|
| P/E | {fmt(ratios.get('pe'))} |
| Debt / Equity | {fmt(ratios.get('debt_to_equity'))} |
| Current ratio | {fmt(ratios.get('current_ratio'))} |
| Gross margin | {fmt(ratios.get('gross_margin'))} |

Overall health (1–5): **{a.get('overall_health')}**

## Factor chain
- Growth: {a.get('growth')}
- Profitability: {a.get('profitability')}
- Solvency: {a.get('solvency')}

## Bull case / Bear case
- **Bull:** {assessment.get('bull_case')}
- **Bear:** {assessment.get('bear_case')}

## Confidence & data gaps
Confidence: `{assessment.get('confidence')}`. Sources: {sources}.
Re-run without `--dry-run` after setting `HKU_GENAI_API_KEY` for full LLM narrative.
"""
