"""Yahoo session closes when the installed XNYS calendar is short. No live network."""
from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from integration.member_a.tools.member_a_common import Store
from integration.member_a.tools.yahoo_tools import build_session_closes, fetch_market_prices, regular_session_close_iso
from integration.requesting import build_request


class _Calendar:
    name = "XNYS"

    def __init__(self, first, last, closes, explode=False):
        self.first_session = pd.Timestamp(first)
        self.last_session = pd.Timestamp(last)
        self.closes = closes
        self.explode = explode
        self.ranges = []

    def sessions_in_range(self, start, end):
        self.ranges.append((pd.Timestamp(start), pd.Timestamp(end)))
        if self.explode or pd.Timestamp(end) > self.last_session:
            from exchange_calendars.errors import DateOutOfBounds
            raise DateOutOfBounds(self, pd.Timestamp(end), "end")
        return pd.DatetimeIndex(list(self.closes))

    def session_close(self, session):
        return self.closes[pd.Timestamp(session).normalize()]


def _frame(dates):
    index = pd.to_datetime(dates)
    return pd.DataFrame({
        "Open": [100.0] * len(dates),
        "High": [101.0] * len(dates),
        "Low": [99.0] * len(dates),
        "Close": [100.5] * len(dates),
        "Adj Close": [100.5] * len(dates),
        "Volume": [1000.0] * len(dates),
        "Dividends": [0.0] * len(dates),
        "Stock Splits": [0.0] * len(dates),
    }, index=index)


class YahooCalendarTests(unittest.TestCase):
    def test_short_calendar_uses_real_close_then_regular_fallback(self):
        calendar = _Calendar("2024-01-02", "2025-12-31", {
            pd.Timestamp("2025-12-31"): pd.Timestamp("2025-12-31 18:00:00", tz="UTC"),
        })
        schedule, checks = build_session_closes(
            calendar, datetime(2023, 11, 13).date(), datetime(2026, 11, 13).date(),
            ["2025-12-31", "2026-11-13", "2026-11-14"],
        )
        self.assertEqual(schedule["2025-12-31"], "2025-12-31T18:00:00+00:00")
        self.assertLessEqual(calendar.ranges[0][1], calendar.last_session)
        self.assertEqual(schedule["2026-11-13"], regular_session_close_iso(datetime(2026, 11, 13).date()))
        expected = datetime(2026, 11, 13, 16, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        self.assertEqual(schedule["2026-11-13"], expected.isoformat())
        self.assertNotIn("2026-11-14", schedule)  # Saturday: no invented session
        self.assertTrue(any(item["rule_id"] == "CALENDAR_FALLBACK_CLOSE" and item["session_date"] == "2026-11-13"
                            for item in checks))

    def test_date_out_of_bounds_does_not_drop_yahoo_bars(self):
        calendar = _Calendar("2024-01-02", "2026-12-31", {}, explode=True)
        request = build_request("MSFT", "2026-11-13", "Asia/Hong_Kong", "prices")
        config = {
            "project_root": None,
            "run_dir": "run",
            "lookback_years": 3,
            "max_tool_calls": 4,
            "target_annual_reports": 1,
            "target_quarters": 1,
            "target_management_events": 1,
            "target_seekingalpha_materials": 1,
            "request_timeout_seconds": 5,
        }
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config["project_root"] = tmp
            store = Store(request, config)
            # Both regular closes are before the Hong Kong end of 2026-11-13.
            frame = _frame(["2026-11-10", "2026-11-12"])
            ticker = type("Ticker", (), {"history": lambda *args, **kwargs: frame})()
            yfinance = types.ModuleType("yfinance")
            yfinance.Ticker = lambda symbol: ticker
            yfinance.__version__ = "test"
            with patch("exchange_calendars.get_calendar", return_value=calendar), \
                    patch.dict(sys.modules, {"yfinance": yfinance}):
                result = fetch_market_prices(
                    "MSFT", "2023-11-13", request["cutoff_at"], store=store, config=config)
            self.assertEqual(result["price_files"][0]["row_count"], 2)
            self.assertEqual(result["price_files"][0]["last_session"], "2026-11-12")
            self.assertTrue(any(item["rule_id"] == "CALENDAR_OUT_OF_BOUNDS" for item in result["quality_checks"]))
            self.assertTrue(any(item["rule_id"] == "CALENDAR_FALLBACK_CLOSE" for item in result["quality_checks"]))
            self.assertTrue(any("16:00 America/New_York" in item for item in result["limitations"]))
            csv_path = Path(tmp) / result["price_files"][0]["path"]
            self.assertEqual(csv_path.read_text(encoding="utf-8").count("\n"), 3)

    def test_empty_yahoo_frame_still_fails(self):
        request = build_request("MSFT", "2026-11-13", "Asia/Hong_Kong", "empty")
        config = {
            "project_root": None, "run_dir": "run", "lookback_years": 3, "max_tool_calls": 4,
            "target_annual_reports": 1, "target_quarters": 1, "target_management_events": 1,
            "target_seekingalpha_materials": 1,
        }
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config["project_root"] = tmp
            store = Store(request, config)
            ticker = type("Ticker", (), {"history": lambda *args, **kwargs: pd.DataFrame()})()
            yfinance = types.ModuleType("yfinance")
            yfinance.Ticker = lambda symbol: ticker
            yfinance.__version__ = "test"
            with patch("exchange_calendars.get_calendar", side_effect=AssertionError("calendar should not be built")), \
                    patch.dict(sys.modules, {"yfinance": yfinance}):
                with self.assertRaises(ValueError) as raised:
                    fetch_market_prices("MSFT", "2023-11-13", request["cutoff_at"], store=store, config=config)
        self.assertIn("no daily bars", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
