"""Mocked DeepSeek/HKU protocol tests. They must not open a real network connection."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from integration.member_a.tools.agent_llm import LLMError, complete
from integration.member_b.analysis_agent import run as run_analysis
from integration.member_b.writer_agent import run as run_writer
from integration.tool_loop import run_tool_loop


def _response(payload):
    stream = io.BytesIO(json.dumps(payload).encode())
    stream.__enter__ = lambda self: self
    stream.__exit__ = lambda self, *args: False
    return stream


def _http_error(code):
    return urllib.error.HTTPError("https://api.deepseek.com/chat/completions", code, "status", None, io.BytesIO(b""))


def _tool_call(name, arguments, call_id):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}


class ProtocolTests(unittest.TestCase):
    def test_deepseek_request_uses_official_endpoint_and_no_web_tools(self):
        seen = {}

        def fake_open(request, timeout):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            seen["headers"] = {key.lower(): value for key, value in request.header_items()}
            seen["body"] = json.loads(request.data.decode())
            return _response({"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {}})

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-not-a-real-key", "DEEPSEEK_MODEL": "deepseek-flash"}, clear=False), \
                patch("urllib.request.urlopen", side_effect=fake_open):
            message, meta = complete(
                [{"role": "user", "content": "ping"}],
                {"llm_provider": "deepseek", "llm_timeout_seconds": 7, "llm_http_attempts": 1},
                tools=[{"type": "function", "function": {"name": "list_finding_slots", "parameters": {}}}],
            )
        self.assertEqual(message["content"], "ok")
        self.assertEqual(meta["provider"], "deepseek")
        self.assertEqual(meta["model"], "deepseek-flash")
        self.assertEqual(urlparse(seen["url"]).hostname, "api.deepseek.com")
        self.assertTrue(seen["url"].endswith("/chat/completions"))
        self.assertEqual(seen["timeout"], 7)
        self.assertTrue(seen["headers"]["authorization"].startswith("Bearer "))
        self.assertNotIn("api-key", seen["headers"])
        self.assertEqual(seen["body"]["thinking"], {"type": "disabled"})
        self.assertEqual([tool["type"] for tool in seen["body"]["tools"]], ["function"])
        self.assertNotIn("deepseek_web_search", json.dumps(seen["body"]))
        self.assertNotIn("unit-test-not-a-real-key", json.dumps(meta))

    def test_hku_provider_keeps_api_key_header(self):
        seen = {}

        def fake_open(request, timeout):
            del timeout
            seen["url"] = request.full_url
            seen["headers"] = {key.lower(): value for key, value in request.header_items()}
            return _response({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

        with patch.dict(os.environ, {"HKU_GENAI_API_KEY": "unit-test-not-a-real-key", "DEEPSEEK_API_KEY": ""}, clear=False), \
                patch("urllib.request.urlopen", side_effect=fake_open):
            complete([{"role": "user", "content": "ping"}], {"llm_provider": "hku", "llm_http_attempts": 1})
        self.assertIn("/foundry/student/models/chat/completions", seen["url"])
        self.assertIn("api-key", seen["headers"])
        self.assertNotIn("authorization", seen["headers"])

    def test_missing_key_does_not_open_http(self):
        def blocked(*args, **kwargs):
            raise AssertionError("HTTP was attempted")

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "HKU_GENAI_API_KEY": ""}, clear=False), \
                patch("urllib.request.urlopen", side_effect=blocked):
            with self.assertRaises(LLMError) as raised:
                complete([{"role": "user", "content": "ping"}], {"llm_provider": "deepseek"})
        self.assertIn("DEEPSEEK_API_KEY", str(raised.exception))

    def test_retry_bound_then_failure(self):
        calls = {"n": 0}

        def fake_open(request, timeout):
            del request, timeout
            calls["n"] += 1
            raise _http_error(429)

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-not-a-real-key"}, clear=False), \
                patch("urllib.request.urlopen", side_effect=fake_open):
            with self.assertRaises(LLMError):
                complete([{"role": "user", "content": "ping"}],
                         {"llm_provider": "deepseek", "llm_http_attempts": 3})
        self.assertEqual(calls["n"], 3)

    def test_malformed_response(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-not-a-real-key"}, clear=False), \
                patch("urllib.request.urlopen", return_value=_response({"choices": []})):
            with self.assertRaises(LLMError):
                complete([{"role": "user", "content": "ping"}],
                         {"llm_provider": "deepseek", "llm_http_attempts": 1})

    def test_unknown_tool_is_rejected_and_not_executed(self):
        executed = {"shell": 0, "submit": 0}

        def complete_stub(messages, config, tools=None, json_output=False):
            del config, tools, json_output
            turns = sum(1 for item in messages if item.get("role") == "assistant")
            if turns == 0:
                return _tool_call("shell", {"cmd": "rm -rf /"}, "bad"), {"provider": "deepseek", "model": "deepseek-flash"}
            if turns == 1:
                body = json.loads(messages[-1]["content"])
                self.assertEqual(body["error"], "UNKNOWN_TOOL")
                return _tool_call("submit_ok", {}, "ok"), {"provider": "deepseek", "model": "deepseek-flash"}
            return {"role": "assistant", "content": "done", "tool_calls": []}, {"provider": "deepseek", "model": "deepseek-flash"}

        def submit(_arguments):
            executed["submit"] += 1
            return {"accepted": True, "payload": {"ok": True}}

        with tempfile.TemporaryDirectory() as tmp, patch("integration.tool_loop.complete", side_effect=complete_stub):
            payload = run_tool_loop(
                [{"role": "user", "content": "go"}],
                [],
                {"submit_ok": submit},
                {"max_tool_calls": 4, "llm_trace_path": str(Path(tmp) / "llm_tools.jsonl")},
                required_submit="submit_ok",
                stage="analysis",
            )
            trace = (Path(tmp) / "llm_tools.jsonl").read_text(encoding="utf-8")
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(executed["shell"], 0)
        self.assertEqual(executed["submit"], 1)
        self.assertIn("UNKNOWN_TOOL", trace)
        self.assertNotIn("rm -rf", trace)

    def test_budget_without_submit_fails_closed(self):
        def complete_stub(messages, config, tools=None, json_output=False):
            del messages, config, tools, json_output
            return _tool_call("submit_ok", {}, "x"), {"provider": "deepseek", "model": "deepseek-flash"}

        def reject(_arguments):
            return {"error": "UNGROUNDED_STATEMENT"}

        with patch("integration.tool_loop.complete", side_effect=complete_stub):
            with self.assertRaises(LLMError) as raised:
                run_tool_loop(
                    [{"role": "user", "content": "go"}], [], {"submit_ok": reject},
                    {"max_tool_calls": 2}, required_submit="submit_ok", stage="analysis",
                )
        self.assertIn("template text was not substituted", str(raised.exception))


class AuthorTests(unittest.TestCase):
    def _bundle(self):
        def metric(metric_id, name, value, end):
            return {
                "metric_id": metric_id, "name": name, "value": value, "unit": "currency",
                "currency": "USD", "period_start": "2025-01-01", "period_end": end,
                "period_type": "quarter", "evidence_id": "E1",
            }
        return {
            "bundle_id": "DEMO_MSFT_B001",
            "revision": 1,
            "cutoff_at": "2026-11-13T23:59:59+08:00",
            "company": {"company_id": "MSFT"},
            "metrics": [
                metric("M0", "revenue", 100, "2025-09-30"),
                metric("M1", "revenue", 110, "2026-09-30"),
            ],
            "evidence": [{
                "evidence_id": "E1", "source_id": "S1",
                "quote": "Revenue commentary without extra figures.",
            }],
        }

    def _script(self):
        def complete_stub(messages, config, tools=None, json_output=False):
            del config, tools, json_output
            stage_calls = [item for item in messages if item.get("role") == "tool"]
            if not any(item.get("role") == "assistant" for item in messages):
                name = "list_finding_slots" if "analysis" in messages[0]["content"] or True else "list_accepted_statements"
                # The first user content differs by stage; both start by listing.
                name = "list_finding_slots" if "claim_id" not in messages[-1]["content"] else "list_accepted_statements"
                if "Write the report paragraphs" in messages[1]["content"]:
                    name = "list_accepted_statements"
                else:
                    name = "list_finding_slots"
                return _tool_call(name, {}, "list"), {"provider": "deepseek", "model": "deepseek-flash"}
            payload = json.loads(stage_calls[-1]["content"])
            if payload.get("accepted"):
                return {"role": "assistant", "content": "done", "tool_calls": []}, {
                    "provider": "deepseek", "model": "deepseek-flash"}
            if "slots" in payload:
                statements = []
                for slot in payload["slots"]:
                    name = slot.get("required_name") or "metric"
                    if slot.get("require_calculated"):
                        text = f"The calculated {name} is " + "".join(slot["required_display"])
                    elif slot["required_display"]:
                        text = f"The {name} is " + "".join(slot["required_display"])
                    elif name != "metric":
                        text = f"The {name} is unavailable and no estimate was made"
                    else:
                        text = "This item is unavailable and no estimate was made"
                    statements.append({"claim_id": slot["claim_id"], "statement": text})
                return _tool_call("submit_finding_statements", {"statements": statements}, "submit"), {
                    "provider": "deepseek", "model": "deepseek-flash"}
            paragraphs = [{
                "claim_id": item["claim_id"],
                "text": "Report paragraph: " + item["statement"],
            } for item in payload["statements"]]
            return _tool_call("submit_paragraphs", {"paragraphs": paragraphs}, "paragraphs"), {
                "provider": "deepseek", "model": "deepseek-flash"}

        return complete_stub

    def test_model_statements_replace_templates_and_writer_keeps_them(self):
        bundle = self._bundle()
        config = {"llm": {"enabled": True, "provider": "deepseek"}, "llm_provider": "deepseek",
                  "output_root": None, "run_dir": None}
        with tempfile.TemporaryDirectory() as tmp, patch("integration.tool_loop.complete", side_effect=self._script()):
            config.update({"output_root": tmp, "run_dir": "run", "llm_trace_path": str(Path(tmp) / "llm_tools.jsonl")})
            analysis = run_analysis(
                {"run_id": "live", "ticker": "MSFT", "cutoff_at": bundle["cutoff_at"]},
                config, bundle,
            )
            self.assertIn(analysis["status"], {"success", "partial"}, analysis.get("errors"))
            statements = [item["statement"] for item in analysis["payload"]["findings"]]
            self.assertTrue(any("revenue growth" in item and "10.0%" in item for item in statements))
            self.assertFalse(any(item.startswith("Revenue growth is calculated from the latest") for item in statements))
            self.assertFalse(any("referenced metric" in item for item in statements))
            written = run_writer(
                {"run_id": "live", "ticker": "MSFT", "cutoff_at": bundle["cutoff_at"]},
                config, bundle, analysis["payload"],
            )
            self.assertIn(written["status"], {"success", "partial"}, written.get("errors"))
            self.assertEqual(written["payload"]["template_id"], "equity_note_v0.1_model_paragraphs")
            report = Path(tmp, written["payload"]["report_path"]).read_text(encoding="utf-8")
            self.assertIn("Report paragraph: The calculated revenue growth is ", report)
            self.assertNotRegex(report, r"[\u4e00-\u9fff]")
            trace = Path(tmp, "llm_tools.jsonl").read_text(encoding="utf-8")
            self.assertIn("submit_finding_statements", trace)
            self.assertIn("submit_paragraphs", trace)

    def test_model_failure_does_not_restore_template(self):
        bundle = self._bundle()

        def fail(messages, config, tools=None, json_output=False):
            del messages, config, tools, json_output
            raise LLMError("service unavailable")

        with patch("integration.tool_loop.complete", side_effect=fail):
            analysis = run_analysis(
                {"run_id": "live", "ticker": "MSFT", "cutoff_at": bundle["cutoff_at"]},
                {"llm": {"enabled": True}, "llm_provider": "deepseek"}, bundle,
            )
        self.assertEqual(analysis["status"], "failed")
        self.assertEqual(analysis["errors"][0]["code"], "LLM_ERROR")
        self.assertEqual(analysis["payload"], {})
        self.assertNotIn("营收同比增长", json.dumps(analysis, ensure_ascii=False))

    def test_bad_base_url_is_rejected_without_http(self):
        def blocked(*args, **kwargs):
            raise AssertionError("HTTP was attempted")

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-not-a-real-key",
                                     "DEEPSEEK_BASE_URL": "https://example.com"}, clear=False), \
                patch("urllib.request.urlopen", side_effect=blocked):
            with self.assertRaises(LLMError):
                complete([{"role": "user", "content": "ping"}], {"llm_provider": "deepseek", "llm_http_attempts": 1})


class ToolLoopFixTests(unittest.TestCase):
    def test_chinese_statement_is_rejected(self):
        from integration.llm_author import _check_text
        self.assertIn("English", _check_text("营收增长 10.0%。", ["10.0%"], []))
        self.assertEqual(_check_text("Revenue grew 10.0% year over year.", ["10.0%"], []), "")
        self.assertIn("referenced metric", _check_text(
            "The referenced metric is 10.0%.", ["10.0%"], [], "revenue growth", True))

    def test_read_tool_success_is_logged_ok(self):
        def complete_stub(messages, config, tools=None, json_output=False):
            del config, tools, json_output
            if not any(item.get("role") == "assistant" for item in messages):
                return _tool_call("list_finding_slots", {}, "list"), {"provider": "deepseek", "model": "deepseek-flash"}
            return _tool_call("submit_ok", {}, "submit"), {"provider": "deepseek", "model": "deepseek-flash"}

        def submit(_arguments):
            return {"accepted": True, "payload": {"ok": True}}

        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "llm_tools.jsonl"
            with patch("integration.tool_loop.complete", side_effect=complete_stub):
                run_tool_loop(
                    [{"role": "user", "content": "go"}], [],
                    {"list_finding_slots": lambda _arguments: {"slots": [{"claim_id": "C1"}], "slot_count": 1},
                     "submit_ok": submit},
                    {"max_tool_calls": 4, "llm_trace_path": str(trace_path)},
                    required_submit="submit_ok", stage="analysis",
                )
            rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        listed = next(row for row in rows if row.get("name") == "list_finding_slots")
        self.assertEqual(listed["status"], "ok")
        self.assertIsNone(listed["error"])
        self.assertNotEqual(listed["status"], "rejected")

    def test_string_top_k_is_accepted_and_bad_top_k_is_explained(self):
        from integration.llm_author import _top_k, author_statements
        self.assertEqual(_top_k("3"), 3)
        self.assertEqual(_top_k("3.0"), 3)
        self.assertEqual(_top_k(3.0), 3)
        self.assertIsNone(_top_k(True))
        self.assertIsNone(_top_k("nope"))
        seen = {}

        def complete_stub(messages, config, tools=None, json_output=False):
            del config, tools, json_output
            tools_so_far = [item for item in messages if item.get("role") == "tool"]
            if not any(item.get("role") == "assistant" for item in messages):
                return _tool_call("search_evidence", {"query": "revenue", "top_k": "3"}, "s"), {
                    "provider": "deepseek", "model": "deepseek-flash"}
            payload = json.loads(tools_so_far[-1]["content"])
            seen.setdefault("search", payload)
            if "slots" not in payload:
                return _tool_call("list_finding_slots", {}, "list"), {"provider": "deepseek", "model": "deepseek-flash"}
            statements = []
            for slot in payload["slots"]:
                text = "模型写明" + "".join(slot["required_display"])
                statements.append({"claim_id": slot["claim_id"], "statement": text})
            return _tool_call("submit_finding_statements", {"statements": statements}, "submit"), {
                "provider": "deepseek", "model": "deepseek-flash"}

        bundle = {
            "bundle_id": "DEMO_MSFT_B001", "revision": 1,
            "metrics": [
                {"metric_id": "M0", "name": "revenue", "value": 100, "unit": "currency", "currency": "USD",
                 "period_end": "2025-09-30", "period_start": "2025-07-01", "period_type": "quarter", "evidence_id": "E1"},
                {"metric_id": "M1", "name": "revenue", "value": 110, "unit": "currency", "currency": "USD",
                 "period_end": "2026-09-30", "period_start": "2026-07-01", "period_type": "quarter", "evidence_id": "E1"},
            ],
            "evidence": [{"evidence_id": "E1", "source_id": "S1", "quote": "Revenue commentary."}],
        }
        with patch("integration.tool_loop.complete", side_effect=complete_stub):
            findings = author_statements([], [], bundle, {"max_tool_calls": 6})
        self.assertNotIn("error", seen["search"])
        self.assertIn("matches", seen["search"])
        self.assertEqual(findings, [])

    def test_bad_top_k_is_rejected_with_a_reason(self):
        captured = {}

        def complete_stub(messages, config, tools=None, json_output=False):
            del config, tools, json_output
            tools_so_far = [item for item in messages if item.get("role") == "tool"]
            if not tools_so_far:
                return _tool_call("search_evidence", {"query": "revenue", "top_k": "nope"}, "bad"), {
                    "provider": "deepseek", "model": "deepseek-flash"}
            captured["result"] = json.loads(tools_so_far[-1]["content"])
            return {"role": "assistant", "content": "stop", "tool_calls": []}, {
                "provider": "deepseek", "model": "deepseek-flash"}

        bundle = AuthorTests()._bundle()
        with patch("integration.tool_loop.complete", side_effect=complete_stub):
            analysis = run_analysis(
                {"run_id": "live", "ticker": "MSFT", "cutoff_at": bundle["cutoff_at"]},
                {"llm": {"enabled": True}, "max_tool_calls": 2}, bundle,
            )
        self.assertEqual(captured["result"]["error"], "INVALID_ARGUMENTS")
        self.assertIn("top_k", captured["result"]["reason"])
        self.assertEqual(analysis["status"], "failed")
        self.assertEqual(analysis["payload"], {})

    def test_search_only_loop_does_not_substitute_templates(self):
        def complete_stub(messages, config, tools=None, json_output=False):
            del messages, config, tools, json_output
            return _tool_call("search_evidence", {"query": "revenue", "top_k": 3}, "s"), {
                "provider": "deepseek", "model": "deepseek-flash"}

        bundle = AuthorTests()._bundle()
        with patch("integration.tool_loop.complete", side_effect=complete_stub):
            analysis = run_analysis(
                {"run_id": "live", "ticker": "MSFT", "cutoff_at": bundle["cutoff_at"]},
                {"llm": {"enabled": True}, "max_tool_calls": 2}, bundle,
            )
        self.assertEqual(analysis["status"], "failed")
        self.assertIn("template text was not substituted", analysis["errors"][0]["message"])
        self.assertEqual(analysis["payload"], {})
        self.assertNotIn("营收同比增长", json.dumps(analysis, ensure_ascii=False))

    def test_large_bundle_slots_stay_bounded(self):
        from integration.llm_author import limit_live_findings
        derived = [{
            "metric_id": "D1", "name": "revenue_growth_yoy", "value": 0.1, "unit": "ratio",
            "calculation_status": "calculated",
        }]
        skeleton = [{
            "claim_id": "Cgrow", "claim_type": "reported_fact", "metric_ids": ["D1"],
            "evidence_ids": [], "statement": "营收同比增长 10.0%。",
        }]
        for index in range(40):
            skeleton.append({
                "claim_id": f"G{index}", "claim_type": "management_guidance",
                "metric_ids": [], "evidence_ids": [f"E{index}"],
                "statement": "管理层指引摘录",
            })
        chosen, note = limit_live_findings(skeleton, derived)
        self.assertLessEqual(len(chosen), 8)
        self.assertIn(chosen[0]["claim_id"], {"Cgrow"})
        self.assertIn("其余", note)
        self.assertGreater(len(skeleton) - len(chosen), 30)
        self.assertEqual(sum(item["claim_type"] == "management_guidance" for item in chosen), 1)

    def test_scenario_sharing_a_metric_keeps_unique_claim_ids(self):
        from integration.llm_author import limit_live_findings
        derived = [{
            "metric_id": "D1", "name": "gross_margin", "value": 0.45, "unit": "ratio",
            "calculation_status": "calculated",
        }]
        skeleton = [
            {"claim_id": "Cmargin", "claim_type": "reported_fact", "metric_ids": ["D1"],
             "evidence_ids": [], "statement": "当季毛利率 45.0%。"},
            {"claim_id": "Cscenario", "claim_type": "scenario_assumption", "metric_ids": ["D1"],
             "evidence_ids": [], "assumption_ids": ["ASM001"], "statement": "情景假设"},
        ]
        for index in range(12):
            skeleton.append({
                "claim_id": f"G{index}", "claim_type": "management_guidance",
                "metric_ids": [], "evidence_ids": [f"E{index}"], "statement": "管理层指引",
            })
        chosen, _note = limit_live_findings(skeleton, derived)
        ids = [item["claim_id"] for item in chosen]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count("Cscenario"), 1)
        self.assertIn("Cmargin", ids)
        self.assertIn("Cscenario", ids)

    def test_parallel_batch_still_runs_a_valid_submit(self):
        calls = {"submit": 0}

        def complete_stub(messages, config, tools=None, json_output=False):
            del messages, config, tools, json_output
            return {"role": "assistant", "content": None, "tool_calls": [
                {"id": "list", "type": "function",
                 "function": {"name": "list_finding_slots", "arguments": {}}},
                {"id": "search", "type": "function",
                 "function": {"name": "search_evidence", "arguments": {"query": "revenue", "top_k": 3.0}}},
                {"id": "submit", "type": "function",
                 "function": {"name": "submit_ok", "arguments": "{}"}},
            ]}, {"provider": "deepseek", "model": "deepseek-flash"}

        def submit(_arguments):
            calls["submit"] += 1
            return {"accepted": True, "payload": {"kept": True}}

        with patch("integration.tool_loop.complete", side_effect=complete_stub):
            payload = run_tool_loop(
                [{"role": "user", "content": "go"}], [],
                {"list_finding_slots": lambda _arguments: {"slots": [{"claim_id": "C1"}]},
                 "search_evidence": lambda arguments: {"matches": [], "top_k": arguments["top_k"]},
                 "submit_ok": submit},
                {"max_tool_calls": 2},
                required_submit="submit_ok", stage="analysis",
            )
        self.assertEqual(payload, {"kept": True})
        self.assertEqual(calls["submit"], 1)


if __name__ == "__main__":
    unittest.main()
