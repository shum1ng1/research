# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""Reviewer orchestration behind the exact report-v0.1 public interface."""
from __future__ import annotations

import json
from pathlib import Path

from integration.member_a.schemas.member_a import ContractError, envelope, require, validate_data_bundle, validate_downstream, validate_request
from integration.member_a.tools import agent_llm
from integration.member_a.tools.member_a_common import Store, error_record, now_iso
from integration.member_a.tools.review_checks import check_claim_support, inspect_artifacts, issue, verify_numeric_claims

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "reviewer_agent.md"
SUPPORTS = {"supported", "partially_supported", "unsupported", "uncertain"}


def _tool(name, properties, required, description):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False}}}


REVIEW_TOOLS = [
    _tool("verify_numeric_claims", {}, [], "Run deterministic numeric/period/source checks against the supplied inputs"),
    _tool("check_claim_support", {"judgments": {"type": "array", "items": {
        "type": "object", "properties": {"report_claim_id": {"type": "string"},
        "support": {"type": "string", "enum": sorted(SUPPORTS)}, "rationale": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["report_claim_id", "support", "rationale", "evidence_ids"], "additionalProperties": False}}},
        ["judgments"], "Record evidence-grounded support judgment for every supplied claim exactly once")
]


def _semantic_review(bundle, analysis, report, config, store, numeric_result):
    metrics = {m["metric_id"]: m for m in bundle["metrics"] + analysis["derived_metrics"]}
    evidence = {e["evidence_id"]: e for e in bundle["evidence"]}
    sources = {s["source_id"]: s for s in bundle["sources"]}
    claims = []
    for claim in report["claim_manifest"]:
        attached_evidence = [evidence[e] for e in claim["evidence_ids"] if e in evidence]
        claims.append(dict(claim, attached_metrics=[metrics[m] for m in claim["metric_ids"] if m in metrics],
                           attached_evidence=attached_evidence,
                           attached_sources=[sources[s] for s in sorted({e["source_id"] for e in attached_evidence}) if s in sources]))
    if not claims:
        raise agent_llm.LLMError("Empty claim_manifest cannot receive a full semantic pass")
    context = json.dumps(dict(claims=claims, assumptions=analysis["assumptions"],
                              scenarios=analysis["scenarios"], findings=analysis["findings"],
                              cutoff_at=bundle["cutoff_at"]), ensure_ascii=False)
    if len(context) > config.get("max_review_context_chars", 120000):
        raise agent_llm.LLMError("Review context exceeds budget; split the report into smaller auditable claims")
    messages = [{"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
                {"role": "user", "content": "CHECK_CONTEXT\n" + context}]
    verified = False
    used = 0
    budget = config.get("max_tool_calls", 12)
    for _ in range(budget + 1):
        message, metadata = agent_llm.complete(messages, config, tools=REVIEW_TOOLS)
        store.trace("reviewer", "llm_completion", {}, now_iso(), "success", metadata)
        calls = message.get("tool_calls", [])
        if not calls:
            raise agent_llm.LLMError("Reviewer stopped before completing both verification tools")
        if used + len(calls) > budget:
            raise agent_llm.LLMError("Reviewer tool-call budget exceeded")
        messages.append(dict(role="assistant", content=message.get("content"), tool_calls=calls))
        for call in calls:
            used += 1
            name = call.get("function", {}).get("name")
            start = now_iso()
            try:
                args = json.loads(call["function"]["arguments"])
                if name == "verify_numeric_claims" and args == {}:
                    verified = True
                    result = numeric_result
                elif name == "check_claim_support" and set(args) == {"judgments"}:
                    if not verified:
                        raise ValueError("verify_numeric_claims must run first")
                    judgments = check_claim_support(claims, bundle["evidence"], args["judgments"])
                    store.trace("reviewer", name, {}, start, "success", {"judgments": judgments}, call.get("id"))
                    return judgments
                else:
                    raise ValueError("Unregistered reviewer tool or invalid arguments")
                store.trace("reviewer", name, {}, start, "success", result, call.get("id"))
            except (KeyError, ValueError, TypeError) as exc:
                result = {"error": "INVALID_REVIEW_TOOL_CALL", "reason": str(exc)[:180]}
                store.trace("reviewer", name or "invalid", {}, start, "failed", result, call.get("id"))
            messages.append(dict(role="tool", tool_call_id=call.get("id", "invalid"),
                                 content=json.dumps(result, ensure_ascii=False)))
    raise agent_llm.LLMError("Reviewer tool-call budget exceeded")


def validate_review_result(result):
    require(result, ["review_id", "checked_bundle_id", "checked_bundle_revision", "checked_analysis_id",
                     "checked_analysis_revision", "checked_report_id", "checked_report_revision", "decision",
                     "issues", "checks", "manual_review_items", "limitations"], "ReviewResult")
    require(result["checks"], ["numeric", "citations", "periods", "point_in_time", "claim_support", "final_page_count"], "ReviewChecks")
    if result.get("decision") not in {"pass", "revise", "manual_review"}:
        raise ContractError("invalid review decision")
    for value in result.get("checks", {}).values():
        if value not in {"passed", "failed", "not_checked", "not_applicable"}:
            raise ContractError("invalid review check state")
    for item in result.get("issues", []):
        require(item, ["issue_id", "severity", "rule_id", "target_agent", "report_claim_id", "metric_ids",
                       "evidence_ids", "message", "suggested_fix", "verification_method"], "ReviewIssue")
        if item.get("severity") not in {"critical", "high", "medium", "low"} or item.get("target_agent") not in {"data", "analysis", "writer"}:
            raise ContractError("invalid issue severity or route")


def run_review(request, config, data_bundle, analysis_bundle, report_draft):
    store = None
    try:
        validate_request(request)
        validate_data_bundle(data_bundle)
        validate_downstream(analysis_bundle, report_draft)
        if data_bundle["company"]["company_id"] != request["ticker"] or data_bundle["cutoff_at"] != request["cutoff_at"]:
            raise ContractError("DataBundle does not match ResearchRequest")
        store = Store(request, config)
        issues, checks, manual = inspect_artifacts(request, data_bundle, analysis_bundle, report_draft, store, config)
        numeric = verify_numeric_claims(data_bundle, analysis_bundle, report_draft["claim_manifest"])
        issues.extend(numeric["issues"])
        manual.extend(numeric["uncertain"])
        if any(i["rule_id"] in {"FIN_PERIOD_MISMATCH", "INVALID_TTM_WINDOW", "NORMALIZATION_MISMATCH"} for i in issues):
            checks["periods"] = "failed"
        checks["numeric"] = "failed" if numeric["issues"] else "not_checked" if numeric["uncertain"] else "passed"
        store.trace("reviewer", "deterministic_checks", {}, now_iso(), "success",
                    {"checks": checks, "issue_count": len(issues), "manual_review_items": manual})
        warnings, errors = [], []
        mode = config.get("review_mode", "llm")
        if mode == "llm":
            try:
                judgments = _semantic_review(data_bundle, analysis_bundle, report_draft, config, store, numeric)
                uncertain = False
                for judgment in judgments:
                    support = judgment["support"]
                    if support == "uncertain":
                        uncertain = True
                        manual.append(f"{judgment['report_claim_id']}: {judgment['rationale']}")
                    elif support != "supported":
                        issues.append(dict(issue("CLAIM_SUPPORT_" + support.upper(), judgment["rationale"],
                                                 target="analysis" if support == "unsupported" else "writer",
                                                 severity="high" if support == "unsupported" else "medium",
                                                 claim=judgment["report_claim_id"], evidence=judgment["evidence_ids"]),
                                           verification_method="llm_semantic"))
                checks["claim_support"] = "not_checked" if uncertain else "failed" if any(j["support"] != "supported" for j in judgments) else "passed"
            except agent_llm.LLMError as exc:
                errors.append(error_record("LLM_REVIEW_INCOMPLETE", str(exc)))
                checks["claim_support"] = "not_checked"
                manual.append("LLM evidence review did not finish; no semantic pass was asserted")
        elif mode == "deterministic":
            checks["claim_support"] = "not_checked"
            warnings.append("Explicit deterministic review: semantic support remains not_checked")
            manual.append("Run LLM evidence review or complete a documented human review")
        else:
            raise ContractError("review_mode must be llm or deterministic")
        if any(i["severity"] == "critical" for i in issues) or manual:
            decision = "manual_review"
        elif any(i["severity"] in {"high", "medium"} for i in issues):
            decision = "revise"
        else:
            decision = "pass"
        payload = dict(review_id=f"{request['run_id']}_review_{report_draft['revision']}",
                       checked_bundle_id=data_bundle["bundle_id"], checked_bundle_revision=data_bundle["revision"],
                       checked_analysis_id=analysis_bundle["analysis_id"], checked_analysis_revision=analysis_bundle["revision"],
                       checked_report_id=report_draft["report_id"], checked_report_revision=report_draft["revision"],
                       decision=decision,
                       issues=[dict(issue_id=f"I{i:03d}", **item) for i, item in enumerate(issues, 1)],
                       checks=checks, manual_review_items=sorted(set(manual)),
                       limitations=["Draft review does not certify the final PDF page count until it is exported and checked."] if checks["final_page_count"] == "not_checked" else [])
        if config.get("original_table_sample_size", 10) == 0:
            payload["limitations"].append("Human original-table spot-checks explicitly excluded from this test/draft scope; not final course acceptance.")
        base_id = payload["review_id"]
        attempt = 1
        while (store.root / f"review_{payload['review_id']}.json").exists():
            attempt += 1
            payload["review_id"] = f"{base_id}_attempt{attempt}"
        validate_review_result(payload)
        status = "failed" if errors else "success"
        result = envelope(request, "reviewer", status, payload, warnings, errors,
                          trace_path=store.ref(store.root / "logs" / "reviewer_tools.jsonl"))
        path = store.write_json(store.root / f"review_{payload['review_id']}.json", result)
        result["artifacts"] = [path]
        store.write_json(store.root / f"review_{payload['review_id']}.json", result)
        return result
    except Exception as exc:
        result = envelope(request, "reviewer", "failed", {}, errors=[error_record(
            "INVALID_REVIEW_INPUT" if isinstance(exc, (ContractError, KeyError, TypeError)) else "REVIEW_FAILED",
            str(exc) if isinstance(exc, ContractError) else f"Reviewer failed ({type(exc).__name__})")])
        if store:
            store.write_json(store.root / "review_failure.json", result)
        return result
