"""get_financials(ticker, statement, period) — income / balance / cashflow via yfinance."""

from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf

STATEMENT_MAP = {
    "income": ("income_stmt", "quarterly_income_stmt"),
    "income_statement": ("income_stmt", "quarterly_income_stmt"),
    "balance": ("balance_sheet", "quarterly_balance_sheet"),
    "balance_sheet": ("balance_sheet", "quarterly_balance_sheet"),
    "cashflow": ("cashflow", "quarterly_cashflow"),
    "cash_flow": ("cashflow", "quarterly_cashflow"),
}


def _df_to_records(df: pd.DataFrame, max_cols: int = 4) -> dict[str, Any]:
    if df is None or df.empty:
        return {"columns": [], "rows": {}}
    # columns are period ends (timestamps); take most recent first
    cols = list(df.columns)[:max_cols]
    col_labels = [
        c.date().isoformat() if hasattr(c, "date") else str(c) for c in cols
    ]
    rows: dict[str, list[Any]] = {}
    for idx in df.index:
        values = []
        for c in cols:
            v = df.loc[idx, c]
            if pd.isna(v):
                values.append(None)
            else:
                try:
                    values.append(float(v))
                except (TypeError, ValueError):
                    values.append(v)
        rows[str(idx)] = values
    return {"columns": col_labels, "rows": rows}


def get_financials(
    ticker: str,
    statement: str = "income",
    period: str = "annual",
) -> dict[str, Any]:
    """Fetch a financial statement.

    statement: income | balance | cashflow
    period: annual | quarterly
    """
    key = statement.lower().strip()
    if key not in STATEMENT_MAP:
        raise ValueError(
            f"Unknown statement={statement!r}. Use one of: {list(STATEMENT_MAP)}"
        )
    annual_attr, quarterly_attr = STATEMENT_MAP[key]
    attr = quarterly_attr if period.lower().startswith("q") else annual_attr

    t = yf.Ticker(ticker.upper().strip())
    df = getattr(t, attr, None)
    if callable(df):
        df = df()
    if df is None:
        df = pd.DataFrame()

    payload = _df_to_records(df)
    return {
        "ticker": ticker.upper().strip(),
        "statement": key,
        "period": "quarterly" if period.lower().startswith("q") else "annual",
        "source": f"yfinance.{attr}",
        **payload,
    }


def _pick(rows: dict[str, list], candidates: list[str], col_idx: int = 0) -> float | None:
    lower_map = {k.lower(): k for k in rows}
    for name in candidates:
        if name.lower() in lower_map:
            vals = rows[lower_map[name.lower()]]
            if col_idx < len(vals) and vals[col_idx] is not None:
                return float(vals[col_idx])
    # fuzzy contains
    for name in candidates:
        for k_lower, k_orig in lower_map.items():
            if name.lower() in k_lower:
                vals = rows[k_orig]
                if col_idx < len(vals) and vals[col_idx] is not None:
                    return float(vals[col_idx])
    return None


def extract_key_lines(ticker: str) -> dict[str, Any]:
    """Pull commonly used line items for ratio / CoT injection."""
    income = get_financials(ticker, "income", "annual")
    balance = get_financials(ticker, "balance", "annual")
    cashflow = get_financials(ticker, "cashflow", "annual")

    irows = income.get("rows", {})
    brows = balance.get("rows", {})
    crows = cashflow.get("rows", {})

    # col 0 = most recent annual, col 1 = prior (for YoY)
    revenue_t = _pick(irows, ["Total Revenue", "Operating Revenue", "Revenue"])
    revenue_t1 = _pick(irows, ["Total Revenue", "Operating Revenue", "Revenue"], 1)
    gross_profit = _pick(irows, ["Gross Profit"])
    net_income = _pick(irows, ["Net Income", "Net Income Common Stockholders"])
    ebit = _pick(irows, ["EBIT", "Operating Income"])

    total_debt = _pick(
        brows,
        ["Total Debt", "Long Term Debt And Capital Lease Obligation"],
    )
    equity = _pick(
        brows,
        ["Stockholders Equity", "Total Equity Gross Minority Interest", "Common Stock Equity"],
    )
    current_assets = _pick(brows, ["Current Assets"])
    current_liab = _pick(brows, ["Current Liabilities"])
    cash = _pick(brows, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])

    ocf = _pick(crows, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
    capex = _pick(crows, ["Capital Expenditure", "Purchase Of PPE"])

    yoy_revenue = None
    if revenue_t is not None and revenue_t1 not in (None, 0):
        yoy_revenue = (revenue_t - revenue_t1) / abs(revenue_t1)

    return {
        "ticker": ticker.upper().strip(),
        "periods": {
            "income": income.get("columns", []),
            "balance": balance.get("columns", []),
            "cashflow": cashflow.get("columns", []),
        },
        "lines": {
            "revenue": revenue_t,
            "revenue_prior": revenue_t1,
            "yoy_revenue_growth": yoy_revenue,
            "gross_profit": gross_profit,
            "net_income": net_income,
            "ebit": ebit,
            "total_debt": total_debt,
            "stockholders_equity": equity,
            "current_assets": current_assets,
            "current_liabilities": current_liab,
            "cash": cash,
            "operating_cash_flow": ocf,
            "capital_expenditure": capex,
        },
        "sources": [
            f"[income_statement {income.get('columns', ['?'])[0] if income.get('columns') else 'n/a'}]",
            f"[balance_sheet {balance.get('columns', ['?'])[0] if balance.get('columns') else 'n/a'}]",
            f"[cashflow {cashflow.get('columns', ['?'])[0] if cashflow.get('columns') else 'n/a'}]",
        ],
    }
