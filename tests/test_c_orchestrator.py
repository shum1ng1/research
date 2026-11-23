"""Offline orchestration tests. They must not call Yahoo, SEC, or an LLM endpoint."""
from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from integration.member_a.schemas.member_a import ContractError, envelope
from integration.member_b.tools.price_metrics import read_price_rows
from integration.offline import patched_review
from integration.orchestrator import earliest_target, final_status, orchestrate
from integration.requesting import build_request

ROOT = Path(__file__).resolve().parents[1]


def _issue(target):
    return {
        "issue_id": "I001",
        "severity": "high",
        "rule_id": "SCRIPTED",
        "target_agent": target,
        "report_claim_id": None,
        "metric_ids": [],
        "evidence_ids": [],
        "message": "scripted revise",
        "suggested_fix": "rerun that stage",
        "verification_method": "test",
    }


def _review(request, data, analysis, report, decision, issues):
    payload = {
        "review_id": "scripted",
        "checked_bundle_id": data["bundle_id"],
        "checked_bundle_revision": data["revision"],
        "checked_analysis_id": analysis["analysis_id"],
        "checked_analysis_revision": analysis["revision"],
        "checked_report_id": report["report_id"],
        "checked_report_revision": report["revision"],
        "decision": decision,
        "issues": issues,
        "checks": {
            "numeric": "passed",
            "citations": "passed",
            "periods": "passed",
            "point_in_time": "passed",
            "claim_support": "not_checked" if decision == "manual_review" else "passed",
            "final_page_count": "not_checked",
        },
        "manual_review_items": ["scripted human check"] if decision == "manual_review" else [],
        "limitations": [],
    }
    return envelope(request, "reviewer", "success", payload)


class Scripted:
    def __init__(self, saved, review_plan):
        self.saved = saved
        self.review_plan = list(review_plan)
        self.calls = {"data": 0, "analysis": 0, "writer": 0, "reviewer": 0}

    def data(self, feedback=None):
        del feedback
        self.calls["data"] += 1
        return deepcopy(self.saved["data"])

    def analysis(self, data_payload, feedback=None):
        del data_payload, feedback
        self.calls["analysis"] += 1
        return deepcopy(self.saved["analysis"])

    def writer(self, data_payload, analysis_payload, feedback=None):
        del data_payload, analysis_payload, feedback
        self.calls["writer"] += 1
        return deepcopy(self.saved["writer"])

    def reviewer(self, data_payload, analysis_payload, report_payload):
        self.calls["reviewer"] += 1
        decision, issues = self.review_plan.pop(0)
        return _review(
            self.saved["request"], data_payload, analysis_payload, report_payload, decision, issues,
        )

    def runners(self):
        return {"data": self.data, "analysis": self.analysis, "writer": self.writer, "reviewer": self.reviewer}


class OrchestratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.summary = orchestrate(
            "MSFT", "2026-11-13", "Asia/Hong_Kong", offline=True, output_root=cls.root,
        )
        run = cls.root / "runs" / cls.summary["run_id"]
        cls.saved = {"request": json.loads((run / "request.json").read_text(encoding="utf-8"))}
        for stage in ("data", "analysis", "writer", "reviewer"):
            cls.saved[stage] = json.loads((run / "attempts" / "00" / f"{stage}.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_offline_msft_success_and_files(self):
        summary = self.summary
        self.assertEqual(summary["final_status"], "success", json.dumps(summary, ensure_ascii=False)[:2000])
        self.assertEqual(summary["review_decision"], "pass")
        self.assertEqual(summary["execution_status"], "partial")
        self.assertEqual(summary["cutoff_at"], "2026-11-13T23:59:59+08:00")
        self.assertFalse(summary["network_verified"])
        self.assertEqual(summary["llm_transport"], "synthetic_fixture")
        self.assertIn("不是 DeepSeek 官方", " ".join(summary["limitations"]))
        run = self.root / "runs" / summary["run_id"]
        for stage in ("data", "analysis", "writer", "reviewer"):
            self.assertTrue((run / "attempts" / "00" / f"{stage}.json").is_file())
        self.assertTrue((run / "logs" / "orchestrator.jsonl").is_file())
        self.assertTrue((run / "logs" / "data_tools.jsonl").is_file())
        report = (run / "report.md").read_text(encoding="utf-8")
        self.assertIn("Synthetic demo", report)
        self.assertNotRegex(report, r"[\u4e00-\u9fff]")
        blob = json.dumps(summary)
        self.assertNotIn("/Users/", blob)
        self.assertNotIn("HKU_GENAI_API_KEY=", blob)
        for path in run.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                self.assertNotIn(str(self.root), text, path.name)
        data_payload = self.saved["data"]["payload"]
        self.assertEqual(data_payload["cutoff_at"], summary["cutoff_at"])
        self.assertEqual(self.saved["analysis"]["payload"]["cutoff_at"], summary["cutoff_at"])
        saved_config = json.loads((run / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_config["llm"]["mode"], "synthetic_templates")
        self.assertFalse(saved_config["llm"]["enabled"])
        self.assertFalse((run / "logs" / "llm_tools.jsonl").exists())

    def test_csco_cli(self):
        from integration.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            code = main([
                "--ticker", "CSCO", "--as-of", "2026-11-13", "--timezone", "Asia/Hong_Kong",
                "--offline", "--output-root", tmp,
            ])
            self.assertEqual(code, 0)
            summaries = list(Path(tmp).glob("runs/*/summary.json"))
            self.assertEqual(len(summaries), 1)
            summary = json.loads(summaries[0].read_text(encoding="utf-8"))
            self.assertEqual(summary["ticker"], "CSCO")
            self.assertEqual(summary["final_status"], "success")

    def test_cli_without_flag_stays_offline(self):
        from integration.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            code = main([
                "--ticker", "MSFT", "--as-of", "2026-11-13", "--timezone", "America/New_York",
                "--output-root", tmp, "--run-id", "default_offline",
            ])
            self.assertEqual(code, 0)
            summary = json.loads((Path(tmp) / "runs" / "default_offline" / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["llm_transport"], "synthetic_fixture")
            self.assertTrue(summary["cutoff_at"].endswith("-05:00"))

    def _scripted(self, plan, **kwargs):
        scripted = Scripted(self.saved, plan)
        with tempfile.TemporaryDirectory() as tmp:
            summary = orchestrate(
                "MSFT", "2026-11-13", "Asia/Hong_Kong", offline=True, output_root=tmp,
                runners=scripted.runners(), run_id="scripted", **kwargs,
            )
        return summary, scripted.calls

    def test_revise_writer_then_pass_does_not_rerun_data(self):
        summary, calls = self._scripted([("revise", [_issue("writer")]), ("pass", [])])
        self.assertEqual(summary["final_status"], "success")
        self.assertEqual(summary["rework_rounds"], 1)
        self.assertEqual(calls, {"data": 1, "analysis": 1, "writer": 2, "reviewer": 2})

    def test_revise_analysis_reruns_downstream_only(self):
        summary, calls = self._scripted([("revise", [_issue("analysis")]), ("pass", [])])
        self.assertEqual(summary["final_status"], "success")
        self.assertEqual(calls["data"], 1)
        self.assertEqual(calls["analysis"], 2)
        self.assertEqual(calls["writer"], 2)
        self.assertEqual(calls["reviewer"], 2)

    def test_revise_data_reruns_everything(self):
        summary, calls = self._scripted([
            ("revise", [_issue("writer"), _issue("data")]),
            ("pass", []),
        ])
        self.assertEqual(summary["final_status"], "success")
        self.assertEqual(earliest_target([_issue("writer"), _issue("data")]), "data")
        self.assertEqual(calls, {"data": 2, "analysis": 2, "writer": 2, "reviewer": 2})

    def test_two_rework_round_bound(self):
        summary, calls = self._scripted([("revise", [_issue("writer")])] * 5)
        self.assertEqual(summary["final_status"], "manual_review")
        self.assertEqual(summary["review_decision"], "revise")
        self.assertEqual(summary["rework_rounds"], 2)
        self.assertEqual(calls["reviewer"], 3)
        self.assertEqual(calls["writer"], 3)
        self.assertEqual(calls["data"], 1)

    def test_manual_review_does_not_rework(self):
        summary, calls = self._scripted([("manual_review", [])])
        self.assertEqual(summary["final_status"], "manual_review")
        self.assertEqual(summary["review_decision"], "manual_review")
        self.assertEqual(calls["reviewer"], 1)
        self.assertEqual(summary["execution_status"] in {"success", "partial"}, True)

    def test_partial_execution_can_still_pass_review(self):
        saved = deepcopy(self.saved)
        saved["data"] = dict(saved["data"])
        saved["data"]["status"] = "partial"

        class Partial(Scripted):
            def data(self, feedback=None):
                del feedback
                self.calls["data"] += 1
                return deepcopy(saved["data"])

        scripted = Partial(saved, [("pass", [])])
        with tempfile.TemporaryDirectory() as tmp:
            summary = orchestrate(
                "MSFT", "2026-11-13", "Asia/Hong_Kong", offline=True, output_root=tmp,
                runners=scripted.runners(), run_id="partial",
            )
        self.assertEqual(summary["execution_status"], "partial")
        self.assertEqual(summary["final_status"], "success")
        self.assertEqual(summary["review_decision"], "pass")

    def test_stage_exception_and_schema_failure(self):
        def boom(data_payload, feedback=None):
            del data_payload, feedback
            raise RuntimeError("boom")

        base = Scripted(self.saved, [("pass", [])])
        runners = base.runners()
        runners["analysis"] = boom
        with tempfile.TemporaryDirectory() as tmp:
            summary = orchestrate(
                "MSFT", "2026-11-13", "Asia/Hong_Kong", offline=True, output_root=tmp,
                runners=runners, run_id="explode",
            )
            self.assertEqual(summary["final_status"], "failed")
            log = (Path(tmp) / "runs" / "explode" / "logs" / "orchestrator.jsonl").read_text(encoding="utf-8")
            self.assertIn("RuntimeError", log)
            analysis = json.loads((Path(tmp) / "runs" / "explode" / "attempts" / "00" / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(analysis["errors"][0]["code"], "STAGE_EXCEPTION")
            for stage in ("data", "analysis", "writer", "reviewer"):
                self.assertTrue((Path(tmp) / "runs" / "explode" / "attempts" / "00" / f"{stage}.json").is_file())

        def bad(data_payload, feedback=None):
            del data_payload, feedback
            return {"status": "success"}

        runners["analysis"] = bad
        with tempfile.TemporaryDirectory() as tmp:
            summary = orchestrate(
                "MSFT", "2026-11-13", "Asia/Hong_Kong", offline=True, output_root=tmp,
                runners=runners, run_id="schema",
            )
            self.assertEqual(summary["final_status"], "failed")
            analysis = json.loads((Path(tmp) / "runs" / "schema" / "attempts" / "00" / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(analysis["status"], "failed")
            self.assertEqual(analysis["errors"][0]["code"], "SCHEMA")

    def test_missing_llm_key_does_not_touch_network(self):
        def blocked(*args, **kwargs):
            raise AssertionError("network or market client was called")

        env = os.environ.copy()
        env["HKU_GENAI_API_KEY"] = ""
        env["DEEPSEEK_API_KEY"] = ""
        env.pop("SEC_USER_AGENT", None)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=True), \
                patch("urllib.request.urlopen", side_effect=blocked), \
                patch("integration.member_a.tools.member_a_data.fetch_market_prices", side_effect=blocked), \
                patch("integration.member_a.tools.sec_tools.SECClient.get", side_effect=blocked):
            summary = orchestrate("MSFT", "2026-11-13", "Asia/Hong_Kong", offline=False, output_root=tmp, run_id="nokey")
            self.assertEqual(summary["final_status"], "failed")
            self.assertFalse(summary["network_verified"])
            data = json.loads((Path(tmp) / "runs" / "nokey" / "attempts" / "00" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "failed")
            self.assertTrue(any(item.get("code") == "LLM_ERROR" for item in data.get("errors") or []))
            self.assertTrue(any(item.get("code") == "LLM_ERROR" for item in summary.get("errors") or []))

    def test_request_validation_and_timezones(self):
        hong_kong = build_request("msft", "2026-11-13", "Asia/Hong_Kong", "hk")
        new_york = build_request("CSCO", "2026-11-13", "America/New_York", "ny")
        self.assertEqual(hong_kong["cutoff_at"], "2026-11-13T23:59:59+08:00")
        self.assertEqual(new_york["cutoff_at"], "2026-11-13T23:59:59-05:00")
        self.assertNotEqual(
            datetime.fromisoformat(hong_kong["cutoff_at"]),
            datetime.fromisoformat(new_york["cutoff_at"]),
        )
        self.assertEqual(
            datetime.fromisoformat(hong_kong["cutoff_at"]),
            datetime(2026, 11, 13, 23, 59, 59, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )
        apple = build_request("AAPL", "2026-11-13", "America/New_York", "aapl")
        self.assertEqual(apple["ticker"], "AAPL")
        self.assertEqual(apple["cutoff_at"], "2026-11-13T23:59:59-05:00")
        with self.assertRaises(ContractError):
            build_request("TSLA", "2026-11-13", "Asia/Hong_Kong", "nope")
        with self.assertRaises(ContractError):
            build_request("MSFT", "13-11-2026", "Asia/Hong_Kong", "nope")
        with self.assertRaises(ContractError):
            build_request("MSFT", "2026-11-13", "Not/AZone", "nope")
        failed = orchestrate("TSLA", "2026-11-13", "Asia/Hong_Kong", offline=True)
        self.assertEqual(failed["final_status"], "failed")
        self.assertEqual(failed["errors"][0]["code"], "INVALID_REQUEST")

    def test_price_rows_use_close_timestamp_not_session_date(self):
        cutoff = datetime.fromisoformat("2026-11-13T23:59:59+08:00")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.csv"
            columns = [
                "company_id", "provider_symbol", "session_date", "market_timezone", "bar_close_at_utc",
                "open", "high", "low", "close", "adj_close", "volume", "volume_unit", "currency",
                "adjustment_method", "source_id",
            ]
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerow({
                    "session_date": "2026-11-12", "bar_close_at_utc": "2026-11-12T21:00:00+00:00",
                    "close": "100", "company_id": "MSFT",
                })
                writer.writerow({
                    "session_date": "2026-11-13", "bar_close_at_utc": "2026-11-13T21:00:00+00:00",
                    "close": "999", "company_id": "MSFT",
                })
                writer.writerow({"session_date": "2026-11-10", "close": "50", "company_id": "MSFT"})
            rows, skipped = read_price_rows({"path": "prices.csv"}, cutoff, tmp)
        self.assertEqual(skipped, 1)
        self.assertEqual([(row[0], row[1]) for row in rows], [("2026-11-12", 100.0)])

    def test_real_reviewer_routes_tampered_number_to_writer(self):
        report = deepcopy(self.saved["writer"]["payload"])
        claim = report["claim_manifest"][0]
        old = claim["numeric_mentions"][0]["display_text"]
        claim["text"] = claim["text"].replace(old, "999.0")
        claim["numeric_mentions"][0]["display_text"] = "999.0"
        relative = f"runs/{self.summary['run_id']}/tampered.md"
        destination = self.root / relative
        destination.write_text("# 标题\n\n" + claim["text"] + "\n", encoding="utf-8")
        report["report_path"] = relative
        config = {
            "project_root": str(self.root),
            "run_dir": f"runs/{self.summary['run_id']}",
            "lookback_years": 5,
            "max_tool_calls": 12,
            "target_annual_reports": 1,
            "target_quarters": 2,
            "target_management_events": 1,
            "target_seekingalpha_materials": 1,
            "original_table_sample_size": 0,
            "review_mode": "llm",
        }
        result = patched_review(
            self.saved["request"], config, self.saved["data"]["payload"],
            self.saved["analysis"]["payload"], report,
        )
        self.assertEqual(result["status"], "success", result.get("errors"))
        self.assertEqual(result["payload"]["decision"], "revise", result["payload"].get("issues"))
        self.assertTrue(any(item["target_agent"] == "writer" for item in result["payload"]["issues"]))

    def test_local_launch_files_have_no_secrets(self):
        root = ROOT
        ignore = (root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env\n", ignore if ignore.endswith("\n") else ignore + "\n")
        example = (root / ".env.example").read_text(encoding="utf-8")
        self.assertIn("DEEPSEEK_API_KEY=\n", example)
        self.assertIn("SEC_USER_AGENT=\n", example)
        self.assertNotIn("sk-", example)
        self.assertNotRegex(example, r"DEEPSEEK_API_KEY=\S+")
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("exchange-calendars", requirements)
        launch = json.loads((root / ".vscode" / "launch.json").read_text(encoding="utf-8"))
        names = [item["name"] for item in launch["configurations"]]
        self.assertEqual(names, ["Integration: live DeepSeek MSFT CSCO AAPL"])
        live = launch["configurations"][0]
        self.assertEqual(live["module"], "integration.pack")
        self.assertEqual(live["cwd"], "${workspaceFolder}")
        self.assertIn("--live", live["args"])
        self.assertNotIn("--offline", live["args"])
        script = (root / "Run-Integration.command").read_text(encoding="utf-8")
        self.assertIn('cd "$(dirname "$0")"', script)
        self.assertIn("python -m integration.pack --live --provider deepseek", script)
        self.assertNotIn("--offline", script)
        self.assertNotIn("sk-", script)

    def test_legacy_pipeline_entry_is_unchanged(self):
        pipeline = (ROOT / "pipeline.py").read_text(encoding="utf-8")
        self.assertIn("def run(ticker: str, *, dry_run: bool = False, news_limit: int = 5)", pipeline)
        self.assertIn("from agents.data_agent import collect", pipeline)
        data_agent = (ROOT / "agents" / "data_agent.py").read_text(encoding="utf-8")
        self.assertIn("def collect(ticker: str, news_limit: int = 5)", data_agent)
        self.assertNotIn("def run(", data_agent)

    def test_final_status_matrix(self):
        self.assertEqual(final_status("failed", "pass"), "failed")
        self.assertEqual(final_status("partial", "pass"), "success")
        self.assertEqual(final_status("success", "manual_review"), "manual_review")
        self.assertEqual(final_status("partial", "revise"), "manual_review")


if __name__ == "__main__":
    unittest.main()
