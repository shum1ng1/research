# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""Daily Yahoo OHLCV with exchange-calendar closes and retained raw snapshots."""
from __future__ import annotations

import csv
import json
import math
from datetime import date, datetime, timedelta, timezone

from integration.member_a.schemas.member_a import aware_datetime
from integration.member_a.tools.company_registry import resolve_company
from integration.member_a.tools.member_a_common import now_iso

COLUMNS = ["company_id", "provider_symbol", "session_date", "market_timezone", "bar_close_at_utc",
           "open", "high", "low", "close", "adj_close", "volume", "volume_unit", "currency",
           "adjustment_method", "source_id"]


def clean_bars(records, company, cutoff_at, schedule):
    """schedule maps ISO session dates to real market close (including early closes)."""
    cutoff = aware_datetime(cutoff_at)
    output, checks, seen = [], [], {}
    for record in records:
        session = record["session_date"]
        if session not in schedule:
            checks.append(dict(rule_id="NON_SESSION_BAR", status="failed", session_date=session))
            continue
        close_at = aware_datetime(schedule[session])
        if close_at > cutoff:
            continue
        values = {key: record.get(key) for key in ["open", "high", "low", "close", "adj_close", "volume"]}
        required = [values[k] for k in ["open", "high", "low", "close", "volume"]]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in required):
            checks.append(dict(rule_id="INVALID_BAR_NUMBER", status="failed", session_date=session))
            continue
        o, h, lo, c, vol = (values[k] for k in ["open", "high", "low", "close", "volume"])
        if min(o, h, lo, c) <= 0 or vol < 0 or h < max(o, lo, c) or lo > min(o, h, c):
            checks.append(dict(rule_id="INVALID_OHLCV", status="failed", session_date=session))
            continue
        if values["adj_close"] is not None and (not math.isfinite(values["adj_close"]) or values["adj_close"] <= 0):
            checks.append(dict(rule_id="INVALID_ADJUSTED_PRICE", status="failed", session_date=session))
            continue
        if session in seen:
            if seen[session] != values:
                checks.append(dict(rule_id="CONFLICTING_BAR", status="failed", session_date=session))
                output = [r for r in output if r["session_date"] != session]
            continue
        seen[session] = values
        output.append(dict(company_id=company["company_id"], provider_symbol=company["provider_symbols"]["yahoo"],
                           session_date=session, market_timezone=company["timezone"],
                           bar_close_at_utc=close_at.astimezone(timezone.utc).isoformat(),
                           **values, volume_unit="shares", currency=company["currency"],
                           adjustment_method="yahoo_auto_adjust_false"))
    output.sort(key=lambda r: r["session_date"])
    return output, checks


def fetch_market_prices(company_id, start_date, cutoff_at, *, store, config):
    # Optional dependencies are imported only for live market data.
    import pandas as pd
    import yfinance as yf
    import exchange_calendars as xcals

    company = resolve_company(company_id)
    cutoff = aware_datetime(cutoff_at)
    start = date.fromisoformat(start_date)
    if start > cutoff.date():
        raise ValueError("start_date is after cutoff")
    end = cutoff.date() + timedelta(days=1)
    ticker = yf.Ticker(company["provider_symbols"]["yahoo"])
    frame = ticker.history(start=start.isoformat(), end=end.isoformat(), interval="1d",
                           auto_adjust=False, actions=True, raise_errors=True,
                           timeout=config.get("request_timeout_seconds", 30))
    if frame.empty:
        raise ValueError("Yahoo returned no daily bars")
    records, actions = [], []
    for index, row in frame.iterrows():
        session = index.date().isoformat()
        values = {name: (None if pd.isna(row.get(col)) else float(row[col]))
                  for name, col in [("open", "Open"), ("high", "High"), ("low", "Low"),
                                    ("close", "Close"), ("adj_close", "Adj Close"), ("volume", "Volume")]}
        records.append(dict(session_date=session, **values))
        actions.append(dict(session_date=session, dividends=float(row.get("Dividends", 0)),
                            stock_splits=float(row.get("Stock Splits", 0))))
    calendar = xcals.get_calendar("XNYS", start=start.isoformat(), end=end.isoformat())
    schedule = {}
    for session in calendar.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end - timedelta(days=1))):
        schedule[session.date().isoformat()] = calendar.session_close(session).isoformat()
    raw = json.dumps(dict(provider="yfinance", symbol=company_id, records=records,
                          corporate_actions=actions, exchange_schedule=schedule), allow_nan=False)
    source = store.source(company_id, f"Yahoo daily prices {start_date} to {cutoff.date()}",
                           "YAHOO_FINANCE", "market_snapshot", f"https://finance.yahoo.com/quote/{company_id}/history/",
                           raw, available_at=now_iso(), role="transport_snapshot", access_method="yfinance",
                           provider_version=getattr(yf, "__version__", "unknown"))
    cleaned, checks = clean_bars(records, company, cutoff_at, schedule)
    target = store.root / "processed" / f"{company_id}_prices_{source['sha256'][:12]}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for record in cleaned:
            writer.writerow(dict(record, source_id=source["source_id"]))
    return dict(sources=[source], price_files=[dict(path=store.ref(target), source_id=source["source_id"],
                columns=COLUMNS, row_count=len(cleaned), currency="USD", volume_unit="shares",
                adjustment_method="yahoo_auto_adjust_false", available_at_policy="per_bar_exchange_close",
                first_session=cleaned[0]["session_date"] if cleaned else None,
                last_session=cleaned[-1]["session_date"] if cleaned else None)],
                quality_checks=checks, limitations=["Downloaded adjusted/split-adjusted history is not a vintage archive; future corporate actions or provider revisions may affect historical prices."])
