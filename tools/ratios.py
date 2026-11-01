"""calculate_ratio(...) — all ratios recomputed in Python; LLM must not invent them."""

from __future__ import annotations

from typing import Any

import yfinance as yf

from tools.financials import extract_key_lines


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return float(num) / float(den)


def calculate_ratio(
    ticker: str,
    *,
    pe: bool = True,
    debt_to_equity: bool = True,
    current_ratio: bool = True,
    gross_margin: bool = True,
) -> dict[str, Any]:
    """Compute requested ratios from yfinance fundamentals. Missing → null."""
    ticker = ticker.upper().strip()
    lines_payload = extract_key_lines(ticker)
    lines = lines_payload["lines"]

    t = yf.Ticker(ticker)
    info = t.info or {}

    pe_val = None
    if pe:
        # Prefer trailing PE from info; else price / EPS if available
        raw = info.get("trailingPE") or info.get("forwardPE")
        if raw is not None:
            try:
                pe_val = float(raw)
            except (TypeError, ValueError):
                pe_val = None
        if pe_val is None:
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            eps = info.get("trailingEps")
            pe_val = _safe_div(
                float(price) if price is not None else None,
                float(eps) if eps is not None else None,
            )

    dte = None
    if debt_to_equity:
        dte = _safe_div(lines.get("total_debt"), lines.get("stockholders_equity"))
        # yfinance sometimes exposes debtToEquity as percent (e.g. 150.2)
        if dte is None and info.get("debtToEquity") is not None:
            try:
                dte = float(info["debtToEquity"]) / 100.0
            except (TypeError, ValueError):
                dte = None

    cr = None
    if current_ratio:
        cr = _safe_div(lines.get("current_assets"), lines.get("current_liabilities"))
        if cr is None and info.get("currentRatio") is not None:
            try:
                cr = float(info["currentRatio"])
            except (TypeError, ValueError):
                cr = None

    gm = None
    if gross_margin:
        gm = _safe_div(lines.get("gross_profit"), lines.get("revenue"))
        if gm is None and info.get("grossMargins") is not None:
            try:
                gm = float(info["grossMargins"])
            except (TypeError, ValueError):
                gm = None

    return {
        "ticker": ticker,
        "ratios": {
            "pe": pe_val,
            "debt_to_equity": dte,
            "current_ratio": cr,
            "gross_margin": gm,
        },
        "lines": lines,
        "periods": lines_payload.get("periods"),
        "sources_cited": lines_payload.get("sources", []) + ["[yfinance.info]"],
        "note": "All ratios computed in Python from yfinance; null means unavailable.",
    }
