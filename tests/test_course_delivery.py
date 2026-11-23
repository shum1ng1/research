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
from integration.member_a.tools.review_checks import judge_page_count
from integration.member_b.analysis_agent import run as run_analysis
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
            ],
            "evidence": [{"evidence_id": "E1", "source_id": "S1", "quote": "Revenue commentary."}],
        }
        request = {"run_id": "chart", "ticker": "MSFT", "cutoff_at": bundle["cutoff_at"], "language": "zh-CN"}
        with tempfile.TemporaryDirectory() as tmp:
            config = {"output_root": tmp, "run_dir": "run", "llm": {"enabled": False}}
            analysis = run_analysis(request, config, bundle)
            self.assertIn(analysis["status"], {"success", "partial"}, analysis.get("errors"))
            written = run_writer(request, config, bundle, analysis["payload"])
            self.assertIn(written["status"], {"success", "partial"}, written.get("errors"))
            payload = written["payload"]
            self.assertTrue(payload["charts"])
            self.assertIsInstance(payload["page_count"], int)
            self.assertGreaterEqual(payload["page_count"], 1)
            self.assertLessEqual(payload["page_count"], 10)
            pdf = Path(tmp) / payload["pdf_path"]
            self.assertTrue(pdf.is_file())
            from pypdf import PdfReader
            reader = PdfReader(str(pdf))
            self.assertEqual(len(reader.pages), payload["page_count"])
            page_text = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertNotIn("![chart]", page_text)
            self.assertNotRegex(page_text, r"[\u4e00-\u9fff]")
            self.assertIn("Chart:", page_text)
            drawing = reader.pages[0].get_contents().get_data().decode("latin1", errors="replace")
            self.assertIn("calculated metrics", drawing)
            self.assertIn("revenue_growth_yoy", drawing)
            chart = payload["charts"][0]
            self.assertTrue((Path(tmp) / chart["path"]).is_file())
            claim = next(item for item in payload["claim_manifest"] if item["report_claim_id"] == "RCHART")
            self.assertTrue(claim["numeric_mentions"])
            for mention in claim["numeric_mentions"]:
                self.assertIn(mention["display_text"], claim["text"])
                self.assertIn(mention["display_text"], Path(tmp, chart["path"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
