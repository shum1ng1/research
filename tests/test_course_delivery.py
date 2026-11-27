"""Charts, A4 page count, AAPL, and local transcript manifests. No network."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from integration.local_materials import discover_local_manifests
from integration.member_a.schemas.member_a import ContractError, validate_request
from integration.member_a.tools.company_registry import resolve_company
from integration.member_a.tools.document_tools import load_management_materials
from integration.member_a.tools.member_a_common import Store
from integration.member_a.tools.review_checks import judge_page_count, verify_numeric_claims
from integration.member_b.analysis_agent import run as run_analysis
from integration.member_b.tools.finance_metrics import compute_financial_metrics
from integration.member_b.writer_agent import run as run_writer
from integration.requesting import build_request


def _request(root):
    request = build_request("MSFT", "2026-11-13", "Asia/Hong_Kong", "materials")
    config = {
        "project_root": str(root),
        "run_dir": "run",
        "lookback_years": 3,
        "max_tool_calls": 12,
        "target_annual_reports": 1,
        "target_quarters": 1,
        "target_management_events": 1,
        "target_seekingalpha_materials": 1,
        "allow_manual_import": True,
    }
    return request, config


class CourseDeliveryTests(unittest.TestCase):
    def test_page_count_rule(self):
        self.assertEqual(judge_page_count(None, None), "not_checked")
        self.assertEqual(judge_page_count(2, 2), "passed")
        self.assertEqual(judge_page_count(11, 11), "failed")
        self.assertEqual(judge_page_count(3, 2), "failed")

    def test_aapl_is_accepted_and_unknown_ticker_is_rejected(self):
        company = resolve_company("AAPL")
        self.assertEqual(company["provider_symbols"]["yahoo"], "AAPL")
        self.assertEqual(company["provider_symbols"]["sec_cik"], "0000320193")
        self.assertEqual(company["currency"], "USD")
        self.assertEqual(company["timezone"], "America/New_York")
        request = build_request("AAPL", "2026-11-13", "Asia/Hong_Kong", "aapl")
        validate_request(request)
        with self.assertRaises(ContractError):
            build_request("GOOG", "2026-11-13", "Asia/Hong_Kong", "nope")

    def test_missing_manifest_stays_not_checked_and_local_file_is_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request, config = _request(root)
            store = Store(request, config)
            missing = load_management_materials("MSFT", request["cutoff_at"], None, store=store, config=config)
            self.assertEqual(missing["coverage"][0]["state"], "not_checked")
            transcript = root / "imports" / "MSFT" / "transcript.txt"
            transcript.parent.mkdir(parents=True)
            transcript.write_text("Management discussed demand. No extra figures.\n", encoding="utf-8")
            manifest = {
                "documents": [{
                    "company_id": "MSFT",
                    "category": "management_materials",
                    "source_type": "IR",
                    "document_type": "earnings_transcript",
                    "title": "Local transcript",
                    "source_url": "https://www.microsoft.com/investor/synthetic-local-file",
                    "language": "en",
                    "available_at": "2026-10-30T12:00:00+00:00",
                    "source_timezone": "America/New_York",
                    "local_path": "imports/MSFT/transcript.txt",
                    "access_method": "manual_download",
                }]
            }
            manifest_path = root / "imports" / "MSFT" / "management_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            loaded = load_management_materials(
                "MSFT", request["cutoff_at"], "imports/MSFT/management_manifest.json",
                store=store, config=config,
            )
            self.assertNotEqual(loaded["coverage"][0]["state"], "not_checked")
            self.assertTrue(loaded["evidence"])
            self.assertEqual(loaded["sources"][0]["document_type"], "earnings_transcript")
            found = discover_local_manifests(root, "MSFT")
            self.assertEqual(found["management_manifest_path"], "imports/MSFT/management_manifest.json")
            self.assertIsNone(found["seekingalpha_manifest_path"])
            self.assertIsNone(discover_local_manifests(root, "AAPL")["management_manifest_path"])

    def test_chart_and_pdf_when_metrics_exist(self):
        bundle = {
            "bundle_id": "DEMO_MSFT_B001",
            "revision": 1,
            "cutoff_at": "2026-11-13T23:59:59+08:00",
            "company": {"company_id": "MSFT"},
            "metrics": [
                {"metric_id": "M0", "name": "revenue", "value": 100, "unit": "currency", "currency": "USD",
                 "period_start": "2025-07-01", "period_end": "2025-09-30", "period_type": "quarter", "evidence_id": "E1"},
                {"metric_id": "M1", "name": "revenue", "value": 110, "unit": "currency", "currency": "USD",
                 "period_start": "2026-07-01", "period_end": "2026-09-30", "period_type": "quarter", "evidence_id": "E1"},
                {"metric_id": "EPS", "name": "eps_diluted", "value": 2, "unit": "currency_per_share", "currency": "USD",
                 "period_start": "2026-07-01", "period_end": "2026-09-30", "period_type": "quarter", "evidence_id": "E1"},
            ],
            "price_files": [{"path": "prices.csv", "currency": "USD"}],
            "sources": [
                {"source_id": "S1", "document_type": "10-K", "title": "Microsoft annual filing",
                 "public_date": "2026-11-10", "available_at": "2026-11-10T13:00:00+00:00",
                 "source_url": "https://example.com/annual"},
                {"source_id": "S2", "document_type": "8-K", "title": "Current filing",
                 "public_date": "2026-11-11", "available_at": "2026-11-11T13:00:00+00:00"},
            ],
            "evidence": [
                {"evidence_id": "E1", "source_id": "S1",
                 "quote": "Microsoft is a technology company that develops cloud products for businesses."},
                {"evidence_id": "E2", "source_id": "S2",
                 "quote": "Management discusses the issuance of debt and applicable accounting guidance."},
            ],
        }
        request = {"run_id": "chart", "ticker": "MSFT", "cutoff_at": bundle["cutoff_at"], "language": "zh-CN"}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "prices.csv").write_text(
                "session_date,bar_close_at_utc,close\n"
                "2025-11-13,2025-11-13T21:00:00+00:00,100\n"
                "2026-11-12,2026-11-12T21:00:00+00:00,110\n"
                "2026-11-13,2026-11-13T21:00:00+00:00,900\n",
                encoding="utf-8",
            )
            config = {"output_root": tmp, "data_root": tmp, "run_dir": "run", "report_dir": "reports",
                      "llm": {"enabled": False}}
            analysis = run_analysis(request, config, bundle)
            self.assertIn(analysis["status"], {"success", "partial"}, analysis.get("errors"))
            written = run_writer(request, config, bundle, analysis["payload"])
            self.assertIn(written["status"], {"success", "partial"}, written.get("errors"))
            payload = written["payload"]
            self.assertEqual(payload["pdf_path"], "reports/MSFT.pdf")
            self.assertTrue(payload["charts"])
            self.assertIsInstance(payload["page_count"], int)
            self.assertGreaterEqual(payload["page_count"], 1)
            self.assertLessEqual(payload["page_count"], 10)
            pdf = Path(tmp) / payload["pdf_path"]
            self.assertTrue(pdf.is_file())
            from pypdf import PdfReader
            reader = PdfReader(str(pdf))
            self.assertEqual(len(reader.pages), payload["page_count"])
            page_text = " ".join(" ".join(page.extract_text() or "" for page in reader.pages).split())
            self.assertNotIn("![chart]", page_text)
            self.assertNotRegex(page_text, r"[\u4e00-\u9fff]")
            self.assertIn("Daily closing price (USD)", page_text)
            self.assertIn("2025-11-13", page_text)
            self.assertIn("2026-11-12", page_text)
            self.assertIn("110.00", page_text)
            self.assertNotIn("900.00", page_text)
            self.assertIn("55.0x", page_text)
            self.assertIn("not an annualized multiple", page_text)
            self.assertIn("that develops cloud products for businesses", page_text)
            self.assertNotIn("issuance of debt", page_text)
            self.assertIn("Microsoft annual filing", page_text)
            report = (Path(tmp) / payload["report_path"]).read_text(encoding="utf-8")
            self.assertNotIn("![chart]", report)
            for heading in ("Summary", "Company overview", "Valuation", "Sources and data gaps"):
                self.assertIn(heading, report)
                self.assertIn(heading, page_text)
            self.assertIn("Microsoft Corporation", report)
            self.assertIn("2026-11-13", report)
            chart = payload["charts"][0]
            self.assertIn("<polyline", (Path(tmp) / chart["path"]).read_text(encoding="utf-8"))
            claim = next(item for item in payload["claim_manifest"] if item["report_claim_id"] == "RTABLE")
            self.assertTrue(claim["numeric_mentions"])
            for mention in claim["numeric_mentions"]:
                self.assertIn(mention["display_text"], claim["text"])
                self.assertIn(mention["display_text"], page_text)
            self.assertEqual(verify_numeric_claims(bundle, analysis["payload"], payload["claim_manifest"])["issues"], [])

    def test_revenue_yoy_ignores_annual_rows_and_adjacent_quarters(self):
        def revenue(metric_id, start, end, value, period_type="quarter", currency="USD"):
            return {"metric_id": metric_id, "name": "revenue", "value": value, "unit": "currency",
                    "currency": currency, "period_start": start, "period_end": end, "period_type": period_type}

        comparable = revenue("PRIOR", "2025-04-27", "2025-07-26", 100)
        rows = [
            comparable,
            revenue("FX", "2025-04-27", "2025-07-26", 200, currency="EUR"),
            revenue("ADJACENT", "2026-01-25", "2026-04-25", 110),
            revenue("ANNUAL", "2025-07-27", "2026-07-25", 430, "annual"),
            revenue("CURRENT", "2026-04-26", "2026-07-25", 120),
        ]
        bundle = {"bundle_id": "DEMO_B001", "metrics": rows}
        result = compute_financial_metrics(bundle)["derived_metrics"][0]
        self.assertEqual(result["value"], 0.2)
        self.assertEqual(result["input_metric_ids"], ["CURRENT", "PRIOR"])
        invalid = dict(result, value=round((120 - 430) / 430, 4),
                       input_metric_ids=["CURRENT", "ANNUAL"], formula="(CURRENT - ANNUAL) / abs(ANNUAL)")
        review = verify_numeric_claims(bundle, {"derived_metrics": [invalid], "assumptions": []}, [])
        self.assertEqual([issue["rule_id"] for issue in review["issues"]], ["FIN_PERIOD_MISMATCH"])
        for value in (None, 0):
            comparable["value"] = value
            result = compute_financial_metrics(bundle)["derived_metrics"][0]
            self.assertIsNone(result["value"])
            self.assertEqual(result["calculation_status"], "unavailable")

    def test_currency_display_and_named_fcf_are_not_a_filing_line(self):
        from integration.member_b.tools.formatting import display_string, display_text
        self.assertEqual(display_string(18633000000, "currency"), "$18.6 billion")
        self.assertEqual(display_text(18633000000, "currency"), "18.6")
        self.assertNotIn("18633000000", display_string(18633000000, "currency"))
        bundle = {
            "bundle_id": "DEMO_MSFT_B001",
            "revision": 1,
            "cutoff_at": "2026-11-13T23:59:59+08:00",
            "company": {"company_id": "MSFT", "name": "Microsoft Corporation", "market": "NASDAQ"},
            "metrics": [
                {"metric_id": "M0", "name": "revenue", "value": 100, "unit": "currency", "currency": "USD",
                 "period_start": "2025-07-01", "period_end": "2025-09-30", "period_type": "quarter", "evidence_id": "E1"},
                {"metric_id": "M1", "name": "revenue", "value": 110, "unit": "currency", "currency": "USD",
                 "period_start": "2026-07-01", "period_end": "2026-09-30", "period_type": "quarter", "evidence_id": "E1"},
                {"metric_id": "MC", "name": "operating_cash_flow", "value": 20000000000, "unit": "currency", "currency": "USD",
                 "period_start": "2026-07-01", "period_end": "2026-09-30", "period_type": "quarter", "evidence_id": "E1"},
                {"metric_id": "MX", "name": "capex_cash_outflow", "value": 1367000000, "unit": "currency", "currency": "USD",
                 "period_start": "2026-07-01", "period_end": "2026-09-30", "period_type": "quarter", "evidence_id": "E1"},
            ],
            "evidence": [{"evidence_id": "E1", "source_id": "S1", "quote": "Revenue commentary."}],
            "coverage": [{"category": "management_materials", "source": "management_materials", "state": "not_checked"}],
        }
        request = {"run_id": "money", "ticker": "MSFT", "cutoff_at": bundle["cutoff_at"],
                   "timezone": "Asia/Hong_Kong", "language": "en"}
        with tempfile.TemporaryDirectory() as tmp:
            written = run_writer(request, {"output_root": tmp, "run_dir": "run", "llm": {"enabled": False}},
                                 bundle, run_analysis(request, {"llm": {"enabled": False}}, bundle)["payload"])
            self.assertIn(written["status"], {"success", "partial"}, written.get("errors"))
            report = (Path(tmp) / written["payload"]["report_path"]).read_text(encoding="utf-8")
            self.assertIn("Free cash flow is calculated", report)
            self.assertIn("$18.6 billion", report)
            self.assertNotIn("18633000000", report)
            self.assertNotIn("referenced metric", report.lower())
            self.assertNotIn("revenue growth stays", report.lower())
            self.assertIn("Hong Kong", report)
            self.assertIn("management materials", report.lower())
            self.assertEqual(written["payload"]["charts"], [])
            fcf = next(item for item in written["payload"]["claim_manifest"]
                       if "Free cash flow is calculated" in item["text"])
            self.assertEqual(fcf["claim_type"], "analyst_inference")
            mention = fcf["numeric_mentions"][0]
            self.assertEqual(mention["display_text"], "18.6")
            self.assertEqual(mention["display_unit"], "usd billion")
            self.assertIn(mention["display_text"], fcf["text"])


if __name__ == "__main__":
    unittest.main()
