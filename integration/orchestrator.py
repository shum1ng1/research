"""Sequence Data, Analysis, Writer, and Reviewer.

Execution status (success / partial / failed) is separate from the review
decision (pass / revise / manual_review). At most two rework rounds run after
the initial pass. A revise issue reruns the earliest named stage and every
stage after it.
"""
from __future__ import annotations

import json
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from integration.member_a.schemas.member_a import (
    ContractError,
    envelope,
    validate_data_bundle,
    validate_downstream,
    validate_envelope,
)
from integration.member_a.tools.member_a_review import validate_review_result
from integration.local_materials import discover_local_manifests
from integration.offline import NOTICE, patched_data, patched_review, prepare_fixture
from integration.requesting import build_request, make_run_id

MAX_REWORK_ROUNDS = 2
STAGES = ("data", "analysis", "writer", "reviewer")
PIPELINE = ("data", "analysis", "writer")

LIMITATIONS = [
    "仅支持 MSFT 与 CSCO。as_of 会换成该时区当天 23:59:59 的 cutoff，后续过滤使用这个时刻，不用日期字符串切片。",
    "行情只保留收盘时刻不晚于 cutoff 的 K 线。没有 bar_close_at_utc 的行会被丢掉。",
    "年化波动率含对数和平方根，v0.1 公式校验器不能重算，所以离线结果不给出该数值。",
    "离线模式的工具选择和语义判断是合成替身（synthetic_fixture），不是港大网关，也不是 DeepSeek 官方 API。",
    "港大 Foundry 地址 https://api.hku.hk/foundry/student 是学生网关。默认模型名 DeepSeek-V3.2 只是该网关上的部署名，不是 DeepSeek 官方端点。本次没有验证真实网络调用。",
    "真实 Yahoo 历史价不是时点档案，供应商日后修订可能改变旧 K 线。离线演示不访问 Yahoo。",
    "未导出 PDF，页数检查保持 not_checked。离线演示把人工原表抽查设为 0，并在审核限制里说明。",
    "离线分析句和报告段落是模板，llm.mode 为 synthetic_templates，不能算作模型调用。",
    "实况模式才启用工具调用。数字仍由 Python 计算；模型只能逐字抄写这些展示串。失败不会退回模板。",
    "官方 DeepSeek 是 https://api.deepseek.com/chat/completions ，环境变量 DEEPSEEK_API_KEY，默认模型 deepseek-flash。思考模式关闭，且不挂载网页搜索工具。",
    "Seeking Alpha 没有自动登录或检索。没有本地 manifest 时该项覆盖为 not_checked。电话会逐字稿同样只接受已提供的本地文件。",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _rel(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def earliest_target(issues) -> str | None:
    ranks = []
    for item in issues or []:
        target = item.get("target_agent") if isinstance(item, dict) else None
        if target in PIPELINE:
            ranks.append(PIPELINE.index(target))
    if not ranks:
        return None
    return PIPELINE[min(ranks)]


def execution_status(statuses) -> str:
    useful = [item for item in statuses if item in {"success", "partial", "failed"}]
    if any(item == "failed" for item in useful) or not useful:
        return "failed"
    if any(item == "partial" for item in useful):
        return "partial"
    return "success"


def final_status(execution: str, decision: str | None) -> str:
    if execution == "failed":
        return "failed"
    if decision == "pass":
        return "success"
    if decision in {"manual_review", "revise"}:
        return "manual_review"
    return "failed"


class Orchestrator:
    def __init__(self, ticker, as_of, timezone_name, *, offline, output_root, run_id, language, runners,
                 provider):
        if provider not in {"deepseek", "hku"}:
            raise ContractError("provider must be deepseek or hku")
        self.offline = offline
        self.provider = provider
        self.output_root = Path(output_root).resolve()
        self.run_id = run_id or make_run_id(ticker, as_of, timezone_name)
        self.run_dir = self.output_root / "runs" / self.run_id
        self.log_path = self.run_dir / "logs" / "orchestrator.jsonl"
        self.runners = runners
        self.request = build_request(ticker, as_of, timezone_name, self.run_id, language)
        self.config = self._config()
        self.latest = {}
        self.issues = []
        self.attempt_rows = []
        self.log("orchestrator", "start", extra={"offline": offline, "cutoff_at": self.request["cutoff_at"]})

    def _config(self) -> dict:
        relative_run = _rel(self.run_dir, self.output_root)
        config = {
            "project_root": str(self.output_root),
            "output_root": str(self.output_root),
            "data_root": str(self.output_root),
            "run_dir": relative_run,
            "lookback_years": 5,
            "max_tool_calls": 12,
            "request_timeout_seconds": 30,
            "max_rework_rounds": MAX_REWORK_ROUNDS,
            "collection_mode": "llm",
            "review_mode": "llm",
            "allow_manual_import": True,
            "require_final_pdf": False,
            "offline": self.offline,
            **discover_local_manifests(self.output_root, self.request["ticker"]),
        }
        if self.offline:
            config["llm"] = {"enabled": False, "mode": "synthetic_templates", "provider": None}
            config["llm_provider"] = None
            config.update({
                "target_annual_reports": 1,
                "target_quarters": 2,
                "target_management_events": 1,
                "target_seekingalpha_materials": 1,
                "original_table_sample_size": 0,
                "fixture_notice": NOTICE,
                "llm_transport": "synthetic_fixture",
                "review_transport": "synthetic_fixture",
            })
        else:
            transport = "deepseek_official" if self.provider == "deepseek" else "hku_student_gateway"
            config["llm"] = {"enabled": True, "mode": "tool_calling", "provider": self.provider}
            config["llm_provider"] = self.provider
            config.update({
                "target_annual_reports": 3,
                "target_quarters": 8,
                "target_management_events": 4,
                "target_seekingalpha_materials": 2,
                "original_table_sample_size": 10,
                "llm_transport": transport,
                "review_transport": transport,
            })
        return config

    def log(self, stage, event, status=None, extra=None):
        record = {"ts": _now(), "run_id": self.run_id, "stage": stage, "event": event, "status": status}
        if extra:
            record.update(extra)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _save_stage(self, attempt: int, stage: str, payload: dict) -> str:
        path = self.run_dir / "attempts" / f"{attempt:02d}" / f"{stage}.json"
        _write(path, payload)
        return _rel(path, self.output_root)

    def _fail_envelope(self, stage: str, code: str, message: str) -> dict:
        return envelope(self.request, stage if stage != "writer" else "writer", "failed",
                        errors=[{"code": code, "message": message, "retryable": False}])

    def _validate(self, stage: str, result: dict) -> None:
        validate_envelope(result)
        if result.get("stage") != stage:
            raise ContractError(f"{stage} envelope stage is {result.get('stage')}")
        if result.get("status") == "failed":
            return
        payload = result.get("payload") or {}
        if stage == "data":
            validate_data_bundle(payload)
            if payload.get("cutoff_at") != self.request["cutoff_at"]:
                raise ContractError("DataBundle cutoff_at does not match the request")
        elif stage == "reviewer":
            validate_review_result(payload)
        elif stage == "writer":
            analysis = self.latest["analysis"]["payload"]
            validate_downstream(analysis, payload)
            if analysis.get("cutoff_at") != self.request["cutoff_at"]:
                raise ContractError("Analysis cutoff_at does not match the request")

    def _call(self, stage: str, fn) -> dict:
        try:
            result = fn()
        except Exception as exc:
            self.log(stage, "exception", "failed", extra={"error_type": type(exc).__name__})
            return self._fail_envelope(stage, "STAGE_EXCEPTION", f"{type(exc).__name__}: {exc}")
        if not isinstance(result, dict):
            return self._fail_envelope(stage, "STAGE_SHAPE", "stage did not return an object")
        try:
            self._validate(stage, result)
        except Exception as exc:
            self.log(stage, "schema_rejected", "failed", extra={"error_type": type(exc).__name__})
            failed = self._fail_envelope(stage, "SCHEMA", f"{type(exc).__name__}: {exc}")
            failed["rejected_result"] = result
            return failed
        return result

    def _default_runners(self):
        from integration.member_a.agents.data_agent import run as run_data
        from integration.member_a.agents.reviewer_agent import run as run_review
        from integration.member_b.analysis_agent import run as run_analysis
        from integration.member_b.writer_agent import run as run_writer

        if self.offline:
            self.config["synthetic_accns"] = prepare_fixture(self.output_root, self.request, self.config)

            def data(feedback=None):
                return patched_data(self.request, self.config, feedback)

            def review(data_payload, analysis_payload, report_payload):
                return patched_review(self.request, self.config, data_payload, analysis_payload, report_payload)
        else:
            def data(feedback=None):
                return run_data(self.request, self.config, feedback)

            def review(data_payload, analysis_payload, report_payload):
                return run_review(self.request, self.config, data_payload, analysis_payload, report_payload)

        def analysis(data_payload, feedback=None):
            return run_analysis(self.request, self.config, data_payload, feedback)

        def writer(data_payload, analysis_payload, feedback=None):
            return run_writer(self.request, self.config, data_payload, analysis_payload, feedback)

        return {"data": data, "analysis": analysis, "writer": writer, "reviewer": review}

    def _feedback(self, stage: str, round_number: int):
        if round_number == 0:
            return None
        previous = (self.latest.get(stage) or {}).get("payload") or {}
        return {
            "target_agent": stage,
            "base_revision": int(previous.get("revision") or 0),
            "round": round_number,
            "issues": self.issues,
        }

    def _run_attempt(self, attempt: int, start: str, round_number: int) -> dict:
        runners = self.runners
        start_index = STAGES.index(start)
        statuses = []
        for index, stage in enumerate(STAGES):
            if index < start_index:
                saved = dict(self.latest[stage])
                saved["reused"] = True
                path = self._save_stage(attempt, stage, saved)
                statuses.append(saved.get("status"))
                self.attempt_rows.append({"attempt": attempt, "stage": stage, "status": saved.get("status"),
                                          "reused": True, "path": path})
                self.log(stage, "reuse", saved.get("status"), extra={"attempt": attempt})
                continue
            if statuses and "failed" in statuses and stage != "data":
                note = {"stage": stage, "status": "not_run", "reason": "upstream stage failed",
                        "schema_note": "orchestrator note, not a v0.1 envelope"}
                self.latest[stage] = note
                path = self._save_stage(attempt, stage, note)
                self.attempt_rows.append({"attempt": attempt, "stage": stage, "status": "not_run", "path": path})
                self.log(stage, "skip", "not_run", extra={"attempt": attempt})
                continue
            feedback = self._feedback(stage, round_number) if stage in PIPELINE else None
            if stage == "data":
                result = self._call(stage, lambda feedback=feedback: runners["data"](feedback))
            elif stage == "analysis":
                payload = self.latest["data"]["payload"]
                result = self._call(stage, lambda feedback=feedback, payload=payload: runners["analysis"](payload, feedback))
            elif stage == "writer":
                data_payload = self.latest["data"]["payload"]
                analysis_payload = self.latest["analysis"]["payload"]
                result = self._call(
                    stage,
                    lambda feedback=feedback, data_payload=data_payload, analysis_payload=analysis_payload:
                    runners["writer"](data_payload, analysis_payload, feedback),
                )
            else:
                result = self._call(
                    stage,
                    lambda: runners["reviewer"](
                        self.latest["data"]["payload"],
                        self.latest["analysis"]["payload"],
                        self.latest["writer"]["payload"],
                    ),
                )
            self.latest[stage] = result
            path = self._save_stage(attempt, stage, result)
            statuses.append(result.get("status"))
            decision = (result.get("payload") or {}).get("decision") if stage == "reviewer" else None
            self.attempt_rows.append({
                "attempt": attempt, "stage": stage, "status": result.get("status"),
                "decision": decision, "reused": False, "path": path,
                "errors": result.get("errors") or [],
            })
            self.log(stage, "finish", result.get("status"), extra={"attempt": attempt, "decision": decision})
        review = self.latest.get("reviewer") or {}
        return review

    def run(self) -> dict:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _write(self.run_dir / "request.json", self.request)
        if self.runners is None:
            self.runners = self._default_runners()
        public_config = {key: value for key, value in self.config.items()
                         if key not in {"project_root", "output_root", "data_root", "sec_user_agent", "api_key",
                                        "synthetic_accns", "llm_trace_path"}}
        public_config["project_root"] = "."
        _write(self.run_dir / "config.json", public_config)
        self.config["llm_trace_path"] = str(self.run_dir / "logs" / "llm_tools.jsonl")
        attempt = 0
        start = "data"
        round_number = 0
        decision = None
        while True:
            review = self._run_attempt(attempt, start, round_number)
            review_status = review.get("status")
            decision = (review.get("payload") or {}).get("decision") if review_status == "success" else None
            executed = [row["status"] for row in self.attempt_rows if row["attempt"] == attempt and row["status"] != "not_run"]
            # A failed envelope is failed even when the key is the reviewer decision.
            if review_status != "success" or "failed" in executed:
                decision = None
                break
            if decision == "pass" or decision == "manual_review":
                break
            self.issues = (review.get("payload") or {}).get("issues") or []
            target = earliest_target(self.issues)
            if decision != "revise" or target is None or round_number >= MAX_REWORK_ROUNDS:
                break
            round_number += 1
            attempt += 1
            start = target
            self.log("orchestrator", "rework", extra={"round": round_number, "target": target})
        statuses = [row["status"] for row in self.attempt_rows if row["status"] in {"success", "partial", "failed"}]
        execution = execution_status(statuses)
        # If the last review did not succeed, execution_status already sees failed.
        if review.get("status") != "success":
            execution = "failed"
        final = final_status(execution, decision if review.get("status") == "success" else None)
        report_rel = None
        writer_payload = (self.latest.get("writer") or {}).get("payload") or {}
        report_path = writer_payload.get("report_path")
        if report_path:
            source = self.output_root / report_path
            if source.is_file():
                destination = self.run_dir / "report.md"
                banner = (
                    "Synthetic demo. The figures below come from the offline fixture or this calculation. "
                    "They are not the company's actual financials and they are not investment advice.\n"
                    "Reviewed draft path is the writer report_path. This copy adds this banner only.\n\n"
                    if self.offline else
                    "Draft. The writer payload records the page count. "
                    "Do not treat the figures as checked against the original filings until a human spot check is done.\n\n"
                )
                destination.write_text(banner + source.read_text(encoding="utf-8"), encoding="utf-8")
                report_rel = _rel(destination, self.output_root)
        summary = {
            "run_id": self.run_id,
            "ticker": self.request["ticker"],
            "as_of": self.request["as_of"],
            "timezone": self.request["timezone"],
            "cutoff_at": self.request["cutoff_at"],
            "offline": self.offline,
            "final_status": final,
            "execution_status": execution,
            "review_decision": decision if review.get("status") == "success" else None,
            "rework_rounds": round_number,
            "max_rework_rounds": MAX_REWORK_ROUNDS,
            "attempts": self.attempt_rows,
            "report_path": report_rel,
            "log_path": _rel(self.log_path, self.output_root),
            "llm_transport": self.config.get("llm_transport"),
            "review_transport": self.config.get("review_transport"),
            "network_verified": False,
            "errors": [
                dict(item, stage=row["stage"], attempt=row["attempt"])
                for row in self.attempt_rows for item in (row.get("errors") or [])
                if isinstance(item, dict)
            ],
            "limitations": list(LIMITATIONS),
        }
        if self.offline:
            summary["fixture_notice"] = NOTICE
        _write(self.run_dir / "summary.json", summary)
        self.log("orchestrator", "finish", final, extra={
            "execution_status": execution, "review_decision": summary["review_decision"]})
        return summary


def orchestrate(ticker: str, as_of: str, timezone_name: str, *, offline: bool = False,
                output_root: str = ".", run_id: str | None = None, language: str = "zh-CN",
                runners=None, provider: str = "deepseek") -> dict:
    """Run the four-stage contract. Raises ContractError only for bad CLI inputs before a run directory exists."""
    try:
        worker = Orchestrator(
            ticker, as_of, timezone_name, offline=offline, output_root=output_root,
            run_id=run_id, language=language, runners=runners, provider=provider,
        )
    except ContractError as exc:
        return {
            "final_status": "failed",
            "execution_status": "failed",
            "review_decision": None,
            "errors": [{"code": "INVALID_REQUEST", "message": str(exc)}],
            "ticker": ticker,
            "as_of": as_of,
            "timezone": timezone_name,
            "offline": offline,
            "network_verified": False,
            "limitations": list(LIMITATIONS),
        }
    try:
        return worker.run()
    except Exception as exc:
        # A failure while writing the run still returns a structured object.
        return {
            "final_status": "failed",
            "execution_status": "failed",
            "review_decision": None,
            "errors": [{"code": "ORCHESTRATOR", "message": f"{type(exc).__name__}: {exc}"}],
            "traceback": traceback.format_exc(limit=5),
            "offline": offline,
            "network_verified": False,
            "limitations": list(LIMITATIONS),
        }
