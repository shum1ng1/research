"""get_stock_price(ticker) — last close / current price via yfinance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yfinance as yf


def get_stock_price(ticker: str) -> dict[str, Any]:
    """Return latest price info. Numbers come from yfinance only."""
    t = yf.Ticker(ticker.upper().strip())
    info = t.info or {}
    hist = t.history(period="5d")

    last_close = None
    as_of = None
    if hist is not None and not hist.empty:
        last_close = float(hist["Close"].iloc[-1])
        ts = hist.index[-1]
        as_of = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    else:
        last_close = info.get("currentPrice") or info.get("regularMarketPrice")
        if last_close is not None:
            last_close = float(last_close)
        as_of = datetime.now(timezone.utc).date().isoformat()

    currency = info.get("currency") or "USD"
    name = info.get("shortName") or info.get("longName") or ticker.upper()

    return {
        "ticker": ticker.upper().strip(),
        "name": name,
        "price": last_close,
        "currency": currency,
        "as_of": as_of,
        "source": "yfinance",
    }
