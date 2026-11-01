"""Data agent: gather price, financials, ratios, news (no LLM)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.financials import extract_key_lines, get_financials
from tools.news import get_news_headlines
from tools.price import get_stock_price
from tools.ratios import calculate_ratio


def collect(ticker: str, news_limit: int = 5) -> dict[str, Any]:
    """Fetch live market/fundamentals package for downstream agents."""
    price = get_stock_price(ticker)
    ratios_bundle = calculate_ratio(ticker)
    income = get_financials(ticker, "income", "annual")
    balance = get_financials(ticker, "balance", "annual")
    news = get_news_headlines(ticker, limit=news_limit)

    as_of = price.get("as_of") or datetime.now(timezone.utc).date().isoformat()

    return {
        "ticker": ticker.upper().strip(),
        "as_of": as_of,
        "price": price,
        "ratios": ratios_bundle["ratios"],
        "lines": ratios_bundle.get("lines", {}),
        "periods": ratios_bundle.get("periods"),
        "income_statement": {
            "columns": income.get("columns"),
            "selected_rows": _thin_rows(income.get("rows", {})),
        },
        "balance_sheet": {
            "columns": balance.get("columns"),
            "selected_rows": _thin_rows(balance.get("rows", {})),
        },
        "news": news,
        "sources_cited": ratios_bundle.get("sources_cited", []) + ["[news]"],
        "note": ratios_bundle.get("note"),
    }


def _thin_rows(rows: dict, keep: int = 12) -> dict:
    """Keep a subset of line items so prompts stay small."""
    priority = [
        "Total Revenue",
        "Gross Profit",
        "Operating Income",
        "EBIT",
        "Net Income",
        "Net Income Common Stockholders",
        "Total Debt",
        "Stockholders Equity",
        "Common Stock Equity",
        "Current Assets",
        "Current Liabilities",
        "Cash And Cash Equivalents",
    ]
    out: dict = {}
    lower = {k.lower(): k for k in rows}
    for name in priority:
        if name.lower() in lower:
            out[lower[name.lower()]] = rows[lower[name.lower()]]
        else:
            for k_l, k_o in lower.items():
                if name.lower() in k_l and k_o not in out:
                    out[k_o] = rows[k_o]
                    break
        if len(out) >= keep:
            break
    if not out:
        # fallback: first N keys
        for i, (k, v) in enumerate(rows.items()):
            if i >= keep:
                break
            out[k] = v
    return out
