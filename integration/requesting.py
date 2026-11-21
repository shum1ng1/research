"""Build a v0.1 research request. as_of is a calendar date; cutoff is local end of that day."""
from __future__ import annotations

from datetime import date, datetime, time

from integration.member_a.schemas.member_a import ContractError, validate_request
from integration.member_a.tools.company_registry import resolve_company


def build_request(ticker: str, as_of: str, timezone_name: str, run_id: str, language: str = "zh-CN") -> dict:
    """Return a request whose cutoff is as_of at 23:59:59 in timezone_name.

    validate_request rejects any cutoff that is not that exact local instant.
    """
    if not isinstance(ticker, str) or not ticker.strip():
        raise ContractError("ticker is required")
    ticker = ticker.strip().upper()
    try:
        resolve_company(ticker)
    except ValueError as exc:
        raise ContractError("ticker must be MSFT or CSCO") from exc
    if not isinstance(as_of, str):
        raise ContractError("as_of must be YYYY-MM-DD")
    try:
        day = date.fromisoformat(as_of)
    except ValueError as exc:
        raise ContractError("as_of must be YYYY-MM-DD") from exc
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ContractError("timezone is required")
    from zoneinfo import ZoneInfo
    try:
        zone = ZoneInfo(timezone_name.strip())
    except Exception as exc:
        raise ContractError("timezone is not a known IANA name") from exc
    cutoff = datetime.combine(day, time(23, 59, 59), zone)
    if not isinstance(run_id, str) or not run_id:
        raise ContractError("run_id is required")
    request = {
        "schema_version": "0.1",
        "run_id": run_id,
        "ticker": ticker,
        "as_of": day.isoformat(),
        "timezone": timezone_name.strip(),
        "cutoff_at": cutoff.isoformat(),
        "language": language or "zh-CN",
    }
    validate_request(request)
    return request


def make_run_id(ticker: str, as_of: str, timezone_name: str) -> str:
    safe_zone = timezone_name.strip().replace("/", "_")
    run_id = f"{ticker.strip().upper()}_{as_of}_{safe_zone}"
    if len(run_id) > 100:
        raise ContractError("run_id is longer than 100 characters; pass --run-id")
    return run_id
