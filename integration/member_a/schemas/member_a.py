# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""Validation without optional third-party dependencies. Never rewrites inputs."""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

VERSION = "0.1"
STATES = {"success", "partial", "failed"}
COVERAGE_STATES = {"available", "partial", "not_found", "access_restricted",
                   "not_applicable", "not_checked", "error"}
CHECK_STATES = {"passed", "failed", "not_checked", "not_applicable"}
CLAIM_TYPES = {"reported_fact", "management_guidance", "analyst_inference", "scenario_assumption"}


class ContractError(ValueError):
    pass


def require(obj, fields, label):
    if not isinstance(obj, dict):
        raise ContractError(f"{label} must be an object")
    missing = sorted(set(fields) - set(obj))
    if missing:
        raise ContractError(f"{label} missing: {', '.join(missing)}")


def aware_datetime(value):
    if not isinstance(value, str):
        raise ContractError("timestamp must be an ISO string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("invalid ISO timestamp") from exc
    if result.tzinfo is None:
        raise ContractError("timestamp must include a timezone")
    return result


def validate_request(request):
    require(request, ["schema_version", "run_id", "ticker", "as_of", "timezone",
                      "cutoff_at", "language"], "ResearchRequest")
    if request["schema_version"] != VERSION:
        raise ContractError("schema_version must be 0.1")
    if not isinstance(request["run_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request["run_id"]):
        raise ContractError("run_id must be a safe identifier")
    if request["ticker"] not in {"MSFT", "CSCO", "AAPL"}:
        raise ContractError("ticker must be MSFT, CSCO, or AAPL")
    try:
        day = date.fromisoformat(request["as_of"])
        local = ZoneInfo(request["timezone"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ContractError("invalid as_of or timezone") from exc
    cutoff = aware_datetime(request["cutoff_at"])
    canonical = datetime.combine(day, time(23, 59, 59), local)
    if cutoff != canonical:
        raise ContractError("as_of/timezone and cutoff_at disagree; C must supply local end-of-day")
    if not isinstance(request["language"], str) or not request["language"]:
        raise ContractError("language must be nonempty")
    assert_json(request)
    return cutoff


def assert_json(value):
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ContractError("output must be JSON serializable without NaN/Infinity") from exc


def envelope(request, stage, status, payload=None, warnings=None, errors=None,
             artifacts=None, trace_path=None):
    result = dict(schema_version=VERSION, run_id=request.get("run_id", "invalid") if isinstance(request, dict) else "invalid",
                  stage=stage, status=status, payload=payload or {},
                  warnings=warnings or [], errors=errors or [],
                  artifacts=artifacts or [], trace_path=trace_path)
    assert_json(result)
    return result


def validate_envelope(result):
    require(result, ["schema_version", "run_id", "stage", "status", "payload",
                     "warnings", "errors", "artifacts", "trace_path"], "Envelope")
    if result["schema_version"] != VERSION or result["status"] not in STATES:
        raise ContractError("invalid envelope version/status")
    if result["stage"] not in {"data", "analysis", "writer", "reviewer"}:
        raise ContractError("invalid stage")
    assert_json(result)


def validate_data_bundle(bundle):
    require(bundle, ["bundle_id", "revision", "company", "cutoff_at", "sources",
                     "metrics", "normalization_derivations", "evidence", "price_files",
                     "coverage", "conflicts", "quality_checks"], "DataBundle")
    if isinstance(bundle["revision"], bool) or not isinstance(bundle["revision"], int) or bundle["revision"] < 1:
        raise ContractError("DataBundle revision must be a positive integer")
    aware_datetime(bundle["cutoff_at"])
    for key in ["sources", "metrics", "normalization_derivations", "evidence", "price_files",
                "coverage", "conflicts", "quality_checks"]:
        if not isinstance(bundle[key], list):
            raise ContractError(f"{key} must be an array")
    require(bundle["company"], ["company_id", "name", "market", "provider_symbols", "currency", "timezone"], "CompanyProfile")
    ids = {}
    for group, field in [("sources", "source_id"), ("metrics", "metric_id"), ("evidence", "evidence_id")]:
        values = []
        for item in bundle[group]:
            require(item, [field], group)
            values.append(item[field])
        if any(not isinstance(x, str) or not x for x in values) or len(values) != len(set(values)):
            raise ContractError(f"empty or duplicate {field}")
        ids[group] = set(values)
    for source in bundle["sources"]:
        require(source, ["source_id", "company_id", "title", "source_type", "document_type",
                         "source_url", "language", "available_at", "available_at_precision", "public_date",
                         "source_timezone", "retrieved_at", "access_method", "raw_path", "sha256", "document_version"], "Source")
    for metric in bundle["metrics"]:
        require(metric, ["metric_id", "name", "value", "unit", "currency", "period_start", "period_end",
                         "period_type", "value_type", "accounting_standard", "reporting_basis", "scope",
                         "source_id", "evidence_id", "source_field", "origin", "missing_reason"], "Metric")
        val = metric["value"]
        if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val)):
            raise ContractError("metric value must be a finite JSON number or null")
        if val is None and not metric["missing_reason"]:
            raise ContractError("null metric needs missing_reason")
        if metric["source_id"] is not None and metric["source_id"] not in ids["sources"]:
            raise ContractError("unknown metric source_id")
        if metric["evidence_id"] is not None and metric["evidence_id"] not in ids["evidence"]:
            raise ContractError("unknown metric evidence_id")
    for evidence in bundle["evidence"]:
        require(evidence, ["evidence_id", "source_id", "quote", "locator", "language"], "Evidence")
        if evidence["source_id"] not in ids["sources"]:
            raise ContractError("unknown evidence source_id")
    for coverage in bundle["coverage"]:
        require(coverage, ["category", "source", "state", "requested_count", "obtained_count",
                          "reason_code", "substitute_source_ids", "course_requirement_resolution"], "Coverage")
        if coverage["state"] not in COVERAGE_STATES:
            raise ContractError("invalid coverage state")
    assert_json(bundle)


def validate_downstream(analysis, report):
    require(analysis, ["analysis_id", "revision", "input_bundle_id", "input_bundle_revision",
                       "cutoff_at", "derived_metrics", "findings", "assumptions", "scenarios",
                       "limitations", "warnings"], "AnalysisBundle")
    require(report, ["report_id", "revision", "input_bundle_id", "input_bundle_revision",
                     "input_analysis_id", "input_analysis_revision", "report_path", "claim_manifest",
                     "pdf_path", "page_count"], "ReportDraft")
    for name in ["derived_metrics", "findings", "assumptions", "scenarios"]:
        if not isinstance(analysis[name], list):
            raise ContractError(f"analysis.{name} must be an array")
    if not isinstance(report["claim_manifest"], list):
        raise ContractError("claim_manifest must be an array")
    for group, field in [(analysis["derived_metrics"], "metric_id"), (analysis["findings"], "claim_id"),
                         (analysis["assumptions"], "assumption_id"), (report["claim_manifest"], "report_claim_id")]:
        values = []
        for item in group:
            require(item, [field], "ID-bearing item")
            values.append(item[field])
        if any(not isinstance(x, str) or not x for x in values) or len(set(values)) != len(values):
            raise ContractError(f"invalid or duplicate {field}")
    for value in [analysis["revision"], analysis["input_bundle_revision"], report["revision"], report["input_bundle_revision"], report["input_analysis_revision"]]:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ContractError("input and output revisions must be positive integers")
    for metric in analysis["derived_metrics"]:
        require(metric, ["metric_id", "value", "unit", "currency", "period", "formula_id",
                         "formula", "input_metric_ids", "assumption_ids", "rounding_policy", "calculation_status"], "DerivedMetric")
    for claim in analysis["findings"]:
        require(claim, ["claim_id", "statement", "claim_type", "metric_ids", "evidence_ids", "assumption_ids"], "Finding")
        if claim["claim_type"] not in CLAIM_TYPES:
            raise ContractError("invalid finding claim_type")
    for claim in report["claim_manifest"]:
        require(claim, ["report_claim_id", "text", "section", "paragraph", "analysis_claim_ids",
                        "metric_ids", "evidence_ids", "claim_type", "numeric_mentions"], "ReportClaim")
        if claim["claim_type"] not in CLAIM_TYPES:
            raise ContractError("invalid report claim_type")
        for mention in claim["numeric_mentions"]:
            require(mention, ["display_text", "display_unit", "precision"], "NumericMention")
            if not mention.get("metric_id") and not mention.get("assumption_id"):
                raise ContractError("numeric mention needs metric_id or assumption_id")
    assert_json(analysis)
    assert_json(report)
