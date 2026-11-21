# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""Auditable verification; no substring-based 'known numbers' approval or eval()."""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from integration.member_a.schemas.member_a import aware_datetime
from integration.member_a.tools.member_a_common import known_by
from integration.member_a.tools.normalization import ADDITIVE
from integration.member_a.tools.yahoo_tools import COLUMNS, clean_bars


def issue(rule, message, target="writer", severity="high", claim=None, metrics=None, evidence=None, fix=None):
    return dict(severity=severity, rule_id=rule, target_agent=target,
                report_claim_id=claim, metric_ids=metrics or [], evidence_ids=evidence or [],
                message=message, suggested_fix=fix or "Correct the originating module and rerun dependent stages",
                verification_method="deterministic")


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing/non-numeric value")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite value")
    return result


def evaluate_formula(expression, inputs):
    """Arithmetic, named IDs/metrics, abs/sum/min/max only; never executes code."""
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("Formula too complex")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Name) and node.id in inputs:
            return number(inputs[node.id])
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if number(node.value) not in {Decimal(0), Decimal(1), Decimal(100), Decimal(252), Decimal(365)}:
                raise ValueError("Nonstandard constants must be named assumption inputs")
            return number(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -visit(node.operand) if isinstance(node.op, ast.USub) else visit(node.operand)
        if isinstance(node, ast.BinOp):
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): return a + b
            if isinstance(node.op, ast.Sub): return a - b
            if isinstance(node.op, ast.Mult): return a * b
            if isinstance(node.op, ast.Div): return a / b
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            args = [visit(a) for a in node.args]
            if node.func.id == "abs" and len(args) == 1: return abs(args[0])
            if node.func.id == "sum": return sum(args, Decimal(0))
            if node.func.id == "min" and args: return min(args)
            if node.func.id == "max" and args: return max(args)
        raise ValueError("Unsupported or unbound formula expression")

    return visit(tree)


def _same_period(inputs):
    return len({(m.get("period", m).get("period_start"), m.get("period", m).get("period_end"),
                 m.get("period", m).get("period_type")) for m in inputs}) <= 1


def _series_periods_valid(inputs):
    quarters = sorted([m.get("period", m) for m in inputs], key=lambda m: m.get("period_start") or "")
    if len(quarters) != 4 or any(m.get("period_type") != "quarter" for m in quarters):
        return False
    return all(date.fromisoformat(a["period_end"]) + timedelta(days=1) == date.fromisoformat(b["period_start"])
               for a, b in zip(quarters, quarters[1:]))


def verify_numeric_claims(data_bundle, analysis_bundle, claim_manifest):
    """Public logical interface from report §5. Returns issues and verified IDs."""
    issues, uncertain, verified = [], [], set()
    raw = {m["metric_id"]: m for m in data_bundle["metrics"]}
    derived = {m["metric_id"]: m for m in analysis_bundle["derived_metrics"]}
    assumptions = {a["assumption_id"]: a for a in analysis_bundle["assumptions"] if isinstance(a, dict) and "assumption_id" in a}
    if set(raw) & set(derived) or len(derived) != len(analysis_bundle["derived_metrics"]):
        issues.append(issue("DUPLICATE_METRIC_ID", "Derived metric IDs must be unique and cannot shadow raw metrics", "analysis"))
    index = dict(raw)
    pending = list(derived.values())
    for _ in range(len(pending) + 1):
        progress, remaining = False, []
        for metric in pending:
            mid = metric["metric_id"]
            refs = metric["input_metric_ids"]
            if any(ref in derived and ref not in index for ref in refs):
                remaining.append(metric)
                continue
            progress = True
            if any(ref not in index for ref in refs) or any(a not in assumptions for a in metric["assumption_ids"]):
                issues.append(issue("UNKNOWN_CALCULATION_INPUT", "Calculation references an unknown metric or assumption", "analysis", metrics=[mid]))
                index[mid] = metric
                continue
            inputs = [index[ref] for ref in refs]
            fid = metric["formula_id"].lower()
            if any("currency" in (m.get("unit") or "") for m in inputs) and len({m.get("currency") for m in inputs if "currency" in (m.get("unit") or "")}) > 1:
                issues.append(issue("FIN_CURRENCY_MISMATCH", "Calculation mixes currencies without explicit FX inputs", "analysis", metrics=[mid]))
            if fid in {"operating_margin", "gross_margin", "net_margin", "fcf", "free_cash_flow", "debt_to_equity", "current_ratio"} and not _same_period(inputs):
                issues.append(issue("FIN_PERIOD_MISMATCH", "Ratio/FCF inputs must have matching reporting periods", "analysis", metrics=refs))
            for context in ["scope", "accounting_standard", "reporting_basis"]:
                contexts = {m[context] for m in inputs if m.get(context) is not None}
                if len(contexts) > 1:
                    issues.append(issue("FIN_BASIS_MISMATCH", f"Calculation mixes incompatible {context} contexts", "analysis", metrics=refs))
            if fid in {"ttm", "ttm_sum"} and not _series_periods_valid(inputs):
                issues.append(issue("INVALID_TTM_WINDOW", "TTM needs four consecutive standalone quarters", "analysis", metrics=refs))
            if fid in {"pe", "price_to_earnings"}:
                eps = [m for m in inputs if m.get("name") in {"eps_diluted", "eps_ttm", "trailing_eps"}]
                if not eps:
                    uncertain.append(f"{mid}: P/E denominator identity could not be verified")
                elif eps[0].get("value") is None or number(eps[0]["value"]) <= 0:
                    if metric.get("value") is not None or metric.get("calculation_status") != "not_applicable":
                        issues.append(issue("PE_NONPOSITIVE_EPS", "P/E is not meaningful for missing/nonpositive earnings", "analysis", metrics=[mid]))
                    index[mid] = metric
                    continue
            if metric.get("value") is None:
                if metric.get("calculation_status") not in {"not_applicable", "unavailable"}:
                    issues.append(issue("MISSING_CALCULATION_REASON", "Null calculation needs explicit status", "analysis", metrics=[mid]))
                index[mid] = metric
                continue
            names = {}
            for value in inputs:
                names[value["metric_id"]] = value.get("value")
            by_name = {}
            for value in inputs:
                by_name.setdefault(value.get("name"), []).append(value)
            for name, values in by_name.items():
                if name and len(values) == 1:
                    names[name] = values[0].get("value")
            for aid in metric["assumption_ids"]:
                names[aid] = assumptions[aid].get("value")
            try:
                expected = evaluate_formula(metric["formula"], names)
                policy = metric["rounding_policy"]
                if isinstance(policy, dict) and "decimal_places" in policy:
                    places = policy["decimal_places"]
                    if not isinstance(places, int) or isinstance(places, bool) or not 0 <= places <= 12:
                        raise ValueError("Invalid rounding policy")
                    expected = expected.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
                tolerance = max(abs(expected), Decimal(1)) * Decimal("1e-12")
                if abs(number(metric["value"]) - expected) > tolerance:
                    issues.append(issue("DERIVED_VALUE_MISMATCH", "Derived value does not equal its declared calculation", "analysis", metrics=[mid]))
                else:
                    verified.add(mid)
            except (ValueError, SyntaxError, ArithmeticError, TypeError):
                uncertain.append(f"{mid}: unsupported, missing or invalid calculation; deterministic result not verified")
            index[mid] = metric
        pending = remaining
        if not progress:
            break
    for metric in pending:
        issues.append(issue("CYCLIC_CALCULATION", "Calculation graph has a cycle or unresolved dependency", "analysis", metrics=[metric["metric_id"]]))
    for claim in claim_manifest:
        cid = claim["report_claim_id"]
        for mid in claim["metric_ids"]:
            if mid not in index:
                issues.append(issue("UNKNOWN_METRIC_ID", "Report metric ID does not exist", claim=cid, metrics=[mid]))
        mentions = claim["numeric_mentions"]
        displayed = []
        for mention in mentions:
            item = index.get(mention.get("metric_id")) if mention.get("metric_id") else assumptions.get(mention.get("assumption_id"))
            if item is None:
                issues.append(issue("UNKNOWN_NUMERIC_REFERENCE", "Numeric mention has no registered input", claim=cid))
                continue
            if mention.get("assumption_id") and claim["claim_type"] != "scenario_assumption":
                issues.append(issue("ASSUMPTION_AS_FACT", "Assumption number must be labelled as a scenario assumption", claim=cid))
            if mention["display_text"] not in claim["text"]:
                issues.append(issue("NUMERIC_MANIFEST_MISMATCH", "Declared display_text does not occur in claim text", claim=cid))
            try:
                expected = number(item["value"])
                unit = mention["display_unit"].strip().lower()
                factors = {"usd": Decimal(1), "currency": Decimal(1), "usd million": Decimal("0.000001"),
                           "million usd": Decimal("0.000001"), "usd billion": Decimal("0.000000001"),
                           "billion usd": Decimal("0.000000001"), "shares": Decimal(1),
                           "million shares": Decimal("0.000001"), "ratio": Decimal(1), "multiple": Decimal(1),
                           "usd/share": Decimal(1), "%": Decimal(100), "percentage_points": Decimal(100), "pp": Decimal(100)}
                if unit not in factors:
                    raise ValueError("Unknown display unit")
                if "usd" in unit and item.get("currency") != "USD":
                    raise ValueError("Display currency mismatch")
                if unit in {"%", "pp", "percentage_points"} and item.get("unit") not in {"ratio", "percentage_point_fraction"}:
                    raise ValueError("Percent display requires a ratio input")
                expected_units = ({"percentage_point_fraction"} if unit in {"pp", "percentage_points"}
                                  else {"ratio"} if unit in {"%", "ratio", "multiple"}
                                  else {"shares"} if "shares" in unit
                                  else {"currency_per_share"} if unit == "usd/share"
                                  else {"currency"})
                if item.get("unit") not in expected_units:
                    raise ValueError("Display dimension differs from input unit")
                expected *= factors[unit]
                places = mention["precision"]
                if not isinstance(places, int) or isinstance(places, bool) or not 0 <= places <= 12:
                    raise ValueError("Invalid display precision")
                text = mention["display_text"].strip().replace(",", "").replace("$", "").rstrip("%")
                if text.startswith("(") and text.endswith(")"):
                    text = "-" + text[1:-1]
                got = number(text)
                # Exact rendering under the declared precision; no blanket 1% tolerance.
                rounded = expected.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
                if got != rounded:
                    issues.append(issue("REPORT_NUMBER_MISMATCH", "Displayed number differs from the referenced input under its precision/unit", claim=cid, metrics=[mention.get("metric_id")] if mention.get("metric_id") else []))
                displayed.append(mention["display_text"])
            except (ValueError, ArithmeticError, TypeError):
                issues.append(issue("INVALID_NUMERIC_DISPLAY", "Numeric value, unit, precision or missingness cannot be verified", claim=cid))
        # Report IDs/dates are metadata; do not excuse unrelated small numbers or partial matches.
        residual = re.sub(r"\[[^\]]*\]", "", claim["text"])
        residual = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", residual)
        for text in sorted(displayed, key=len, reverse=True):
            residual = residual.replace(text, " ", 1)
        if re.search(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?", residual):
            issues.append(issue("UNREGISTERED_REPORT_NUMBER", "Claim contains an additional numeric assertion absent from numeric_mentions", claim=cid))
    return dict(issues=issues, uncertain=uncertain, verified_metric_ids=sorted(verified))


def check_claim_support(claims, evidence, judgments):
    """Validate semantic judgments supplied through the registered reviewer tool."""
    expected = {c["report_claim_id"] for c in claims}
    if len(expected) != len(claims) or not isinstance(judgments, list):
        raise ValueError("Duplicate claim IDs or invalid judgments")
    if len(judgments) != len(expected) or {j.get("report_claim_id") for j in judgments} != expected:
        raise ValueError("Must judge every claim exactly once")
    evidence_ids = {e["evidence_id"] for e in evidence}
    by_claim = {c["report_claim_id"]: c for c in claims}
    output = []
    for judgment in judgments:
        if judgment.get("support") not in {"supported", "partially_supported", "unsupported", "uncertain"}:
            raise ValueError("Invalid support enum")
        if not isinstance(judgment.get("rationale"), str) or not judgment["rationale"].strip():
            raise ValueError("A rationale is required")
        attached = set(by_claim[judgment["report_claim_id"]]["evidence_ids"])
        cited = set(judgment.get("evidence_ids", []))
        if not cited <= evidence_ids or not cited <= attached:
            raise ValueError("Judgment references evidence not attached to this claim")
        if judgment["support"] == "supported" and not cited and not by_claim[judgment["report_claim_id"]]["metric_ids"]:
            raise ValueError("Supported claim has neither evidence nor metrics")
        output.append(judgment)
    return output


def original_table_spotchecks(bundle, store, config):
    """Check the audit record of a human comparing at least N original-table fields."""
    count = config.get("original_table_sample_size", 10)
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 100:
        raise ValueError("original_table_sample_size must be 0..100")
    if count == 0:
        return []  # Explicitly limited test/draft scope, disclosed by Reviewer.
    path = config.get("original_table_checks_path")
    if not path:
        return [f"Original-table human spot-check is pending: at least {count} distinct financial fields required"]
    try:
        record = json.loads(store.resolve(path).read_text(encoding="utf-8"))
        if record["input_bundle_id"] != bundle["bundle_id"] or record["input_bundle_revision"] != bundle["revision"] or not record["reviewed_by"].strip():
            raise ValueError()
        metrics = {m["metric_id"]: m for m in bundle["metrics"]}
        sources = {s["source_id"]: s for s in bundle["sources"]}
        checked = set()
        for entry in record["checks"]:
            m = metrics[entry["metric_id"]]
            original = sources[entry["original_source_id"]]
            source = sources[m["source_id"]]
            if source.get("parent_source_id", source["source_id"]) != original["source_id"]:
                raise ValueError()
            if not store.verify_source(original) or entry["sha256"] != original["sha256"] or entry.get("confirmed") is not True:
                raise ValueError()
            text = store.resolve(original.get("extracted_text_path", original["raw_path"])).read_text(encoding="utf-8")
            loc = entry["locator"]
            if not entry["quote"].strip() or text[loc["start"]:loc["end"]] != entry["quote"]:
                raise ValueError()
            if number(entry["normalized_value"]) != number(m["value"]) or any(entry[k] != m[k] for k in ["unit", "currency", "period_start", "period_end"]):
                raise ValueError()
            checked.add(m["name"])
        if len(checked) < count:
            return [f"Only {len(checked)}/{count} distinct original-table fields have signed human checks"]
    except (KeyError, OSError, ValueError, TypeError, ArithmeticError):
        return ["Original-table check record is missing, stale, incomplete or inconsistent with retained files/metrics"]
    return []


def inspect_artifacts(request, bundle, analysis, report, store, config):
    issues, manual = [], []
    manual.extend(original_table_spotchecks(bundle, store, config))
    checks = dict(citations="passed", periods="passed", point_in_time="passed", final_page_count="not_checked")
    sources = {s["source_id"]: s for s in bundle["sources"]}
    evidence = {e["evidence_id"]: e for e in bundle["evidence"]}
    metrics = {m["metric_id"]: m for m in bundle["metrics"]}
    source_cache, text_cache = {}, {}
    for source in bundle["sources"]:
        try:
            if not store.verify_source(source):
                raise ValueError()
            source_cache[source["source_id"]] = store.resolve(source["raw_path"]).read_bytes()
        except (OSError, ValueError):
            issues.append(issue("SOURCE_HASH_OR_FILE", "Source file is missing, outside project, or differs from its saved hash", "data", metrics=[], evidence=[]))
            checks["citations"] = "failed"
        if source["company_id"] != request["ticker"]:
            issues.append(issue("SOURCE_COMPANY_MISMATCH", "Source belongs to a different company", "data", "critical"))
        if source.get("role") != "transport_snapshot":
            try:
                eligible = known_by(source, request["cutoff_at"])
            except (ValueError, KeyError):
                eligible = False
            if not eligible:
                issues.append(issue("SOURCE_AFTER_CUTOFF_OR_UNKNOWN", "Source publication is unknown or later than cutoff", "data", "critical"))
                checks["point_in_time"] = "failed"
    for e in bundle["evidence"]:
        source = sources.get(e["source_id"])
        raw = source_cache.get(e["source_id"])
        if raw is None or source is None:
            continue
        if source.get("role") == "transport_snapshot":
            issues.append(issue("SNAPSHOT_AS_EVIDENCE", "Current transport snapshots cannot directly support historical claims", "data", "critical", evidence=[e["evidence_id"]]))
            checks["point_in_time"] = "failed"
        try:
            if source.get("extracted_text_path"):
                path = source["extracted_text_path"]
                if path not in text_cache:
                    text_cache[path] = store.resolve(path).read_text(encoding="utf-8")
                text = text_cache[path]
                if hashlib.sha256(text.encode()).hexdigest() != source["extracted_text_sha256"]:
                    raise ValueError()
                locator = e["locator"]
                if text[locator["start"]:locator["end"]] != e["quote"]:
                    raise ValueError()
            elif e["quote"] not in raw.decode("utf-8"):
                raise ValueError()
        except (ValueError, KeyError, UnicodeError, OSError, TypeError):
            issues.append(issue("EVIDENCE_QUOTE_MISMATCH", "Quote or locator cannot be verified against the retained original/extracted text", "data", evidence=[e["evidence_id"]]))
            checks["citations"] = "failed"
    for m in bundle["metrics"]:
        if m.get("value") is None:
            continue
        sid = m.get("source_id")
        source = sources.get(sid)
        if source is None or m.get("evidence_id") not in evidence:
            issues.append(issue("RAW_METRIC_NO_EVIDENCE", "Raw metric has no verifiable source/quote", "data", metrics=[m["metric_id"]]))
            continue
        if evidence[m["evidence_id"]]["source_id"] != sid:
            issues.append(issue("METRIC_EVIDENCE_SOURCE_MISMATCH", "Metric and its evidence point to different sources", "data", metrics=[m["metric_id"]]))
        cutoff_day = aware_datetime(request["cutoff_at"]).astimezone(
            ZoneInfo(request["timezone"])).date().isoformat()
        if m["period_end"] > cutoff_day:
            issues.append(issue("METRIC_AFTER_CUTOFF", "Financial period is after cutoff", "data", "critical", metrics=[m["metric_id"]]))
            checks["point_in_time"] = "failed"
        if m.get("origin") == "reported" and source.get("source_type") == "SEC_XBRL_FACT":
            try:
                record = json.loads(source_cache[sid])
                if number(record["fact"]["val"]) * number(m.get("scale_multiplier", 1)) != number(m["value"]):
                    raise ValueError()
                if record["fact"]["end"] != m["period_end"] or (m["value_type"] == "duration" and record["fact"].get("start") != m["period_start"]):
                    checks["periods"] = "failed"
                    raise ValueError()
                snapshot = json.loads(source_cache[record["snapshot_source_id"]])
                facts = snapshot["facts"][record["taxonomy"]][record["concept"]]["units"][record["unit"]]
                if record["fact"] not in facts:
                    raise ValueError()
                parent = sources[record["original_filing_source_id"]]
                if parent.get("accession") != record["fact"]["accn"]:
                    raise ValueError()
                if m.get("source_field") != record["taxonomy"] + ":" + record["concept"]:
                    raise ValueError()
                expected_unit = "shares" if record["unit"] == "shares" else "currency_per_share" if record["unit"] == "USD/shares" else "currency"
                if m["unit"] != expected_unit or m["currency"] != (None if expected_unit == "shares" else "USD"):
                    raise ValueError()
                from integration.member_a.tools.sec_tools import _period
                if _period(record["fact"], m["name"]) != m["period_type"]:
                    checks["periods"] = "failed"
                    raise ValueError()
            except (ValueError, KeyError, TypeError, ArithmeticError):
                issues.append(issue("RAW_FINANCIAL_FACT_MISMATCH", "Metric context/value does not match the retained SEC fact and filing accession", "data", metrics=[m["metric_id"]]))
        elif m.get("origin") == "reported":
            manual.append(f"Raw metric {m['metric_id']} needs original-table spot-check; automatic SEC extraction provenance unavailable")
        elif m.get("origin") != "normalized":
            manual.append(f"Metric {m['metric_id']} has an unverified origin")
    normalized_ids = {d.get("output_metric_id") for d in bundle["normalization_derivations"]}
    for m in bundle["metrics"]:
        if m.get("origin") == "normalized" and m["metric_id"] not in normalized_ids:
            issues.append(issue("NORMALIZATION_MISMATCH", "Normalized metric lacks a derivation", "data", metrics=[m["metric_id"]]))
    for d in bundle["normalization_derivations"]:
        try:
            out = metrics[d["output_metric_id"]]
            a, b = [metrics[x] for x in d["input_metric_ids"]]
            if d.get("operation") != "subtract_cumulative" or out["name"] not in ADDITIVE:
                raise ValueError()
            for field in ["name", "unit", "currency", "period_start", "scope", "reporting_basis", "accounting_standard"]:
                if a.get(field) != b.get(field):
                    raise ValueError()
            if number(out["value"]) != number(a["value"]) - number(b["value"]):
                raise ValueError()
            start = (date.fromisoformat(b["period_end"]) + timedelta(days=1)).isoformat()
            if out["period_start"] != start or out["period_end"] != a["period_end"] or out["period_type"] != "quarter":
                raise ValueError()
            if not 60 <= (date.fromisoformat(out["period_end"]) - date.fromisoformat(start)).days + 1 <= 110:
                raise ValueError()
        except (KeyError, ValueError, TypeError, ArithmeticError):
            issues.append(issue("NORMALIZATION_MISMATCH", "Normalization is not reproducible from its input metrics", "data", metrics=[d.get("output_metric_id")]))
    for price in bundle["price_files"]:
        try:
            snapshot = json.loads(source_cache[price["source_id"]])
            expected, _ = clean_bars(snapshot["records"], bundle["company"], request["cutoff_at"], snapshot["exchange_schedule"])
            with store.resolve(price["path"]).open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != COLUMNS:
                    raise ValueError()
                actual = list(reader)
            if len(actual) != len(expected) or len(actual) != price["row_count"]:
                raise ValueError()
            for a, e in zip(actual, expected):
                e["source_id"] = price["source_id"]
                for column in COLUMNS:
                    value = e[column]
                    if column in {"open", "high", "low", "close", "volume", "adj_close"} and value is not None:
                        if number(a[column]) != number(value):
                            raise ValueError()
                    elif a[column] != ("" if value is None else str(value)):
                        raise ValueError()
        except (KeyError, ValueError, TypeError, ArithmeticError, OSError):
            issues.append(issue("PRICE_FILE_MISMATCH", "Price CSV differs from retained provider data/calendar/cutoff", "data"))
    for item, fields, target in [(analysis, [("input_bundle_id", bundle["bundle_id"]), ("input_bundle_revision", bundle["revision"])], "analysis"),
                                 (report, [("input_bundle_id", bundle["bundle_id"]), ("input_bundle_revision", bundle["revision"]),
                                           ("input_analysis_id", analysis["analysis_id"]), ("input_analysis_revision", analysis["revision"])], "writer")]:
        if any(item[k] != value for k, value in fields):
            issues.append(issue("STALE_INPUT_VERSION", "Module input IDs/revisions do not match reviewed inputs", target))
    if analysis["cutoff_at"] != request["cutoff_at"]:
        issues.append(issue("ANALYSIS_CUTOFF_MISMATCH", "Analysis uses a different cutoff", "analysis", "critical"))
        checks["point_in_time"] = "failed"
    findings = {f["claim_id"]: f for f in analysis["findings"]}
    finding_ids = set(findings)
    report_text = ""
    try:
        report_text = store.resolve(report["report_path"]).read_text(encoding="utf-8")
    except (OSError, ValueError):
        issues.append(issue("REPORT_FILE_MISSING", "report_path must point to an existing readable draft", "writer"))
    if not report["claim_manifest"] and report_text.strip():
        issues.append(issue("EMPTY_CLAIM_MANIFEST", "Nonempty report has no auditable claims", "writer"))
    claimed_text = []
    for claim in report["claim_manifest"]:
        cid = claim["report_claim_id"]
        if report_text and claim["text"] not in report_text:
            issues.append(issue("CLAIM_NOT_IN_REPORT", "Manifest text is not present verbatim in the report", claim=cid))
        claimed_text.append(claim["text"])
        if set(claim["analysis_claim_ids"]) - finding_ids:
            issues.append(issue("UNKNOWN_ANALYSIS_CLAIM", "Report references a nonexistent analysis claim", claim=cid))
        for fid in claim["analysis_claim_ids"]:
            if fid in findings and claim["claim_type"] != findings[fid]["claim_type"]:
                issues.append(issue("CLAIM_TYPE_CHANGED", "Writer changed the finding's fact/guidance/inference/assumption classification", claim=cid))
        for eid in claim["evidence_ids"]:
            if eid not in evidence:
                issues.append(issue("UNKNOWN_EVIDENCE_ID", "Claim references nonexistent evidence", claim=cid, evidence=[eid]))
                checks["citations"] = "failed"
        if not claim["metric_ids"] and not claim["evidence_ids"] and claim["claim_type"] != "scenario_assumption":
            issues.append(issue("CLAIM_WITHOUT_SUPPORT", "Claim has neither metrics nor evidence", claim=cid))
            checks["citations"] = "failed"
    # Any prose paragraph/table row not in the manifest requires explicit review, not silent approval.
    residual = report_text
    for text in sorted(claimed_text, key=len, reverse=True):
        residual = residual.replace(text, "", 1)
    for line in residual.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or re.fullmatch(r"[|:\-\s]+", s):
            continue
        if re.search(r"\d", re.sub(r"\[[^\]]*\]", "", s)):
            issues.append(issue("UNREGISTERED_REPORT_CONTENT", "Report contains numeric text/table content absent from claim_manifest", "writer"))
            break
        if re.search(r"[A-Za-z\u4e00-\u9fff]", s):
            manual.append("Report contains prose outside claim_manifest; semantic coverage is incomplete")
            break
    for conflict in bundle["conflicts"]:
        if conflict.get("state") == "unresolved":
            manual.append("Unresolved data-source/context conflict")
    for item in bundle["coverage"]:
        if item["state"] not in {"available", "not_applicable"} and item.get("course_requirement_resolution") != "accepted_limitation":
            manual.append(f"Coverage {item['category']}/{item['source']} remains {item['state']}; source policy unresolved")
    pdf_path = report.get("pdf_path")
    if pdf_path:
        try:
            from pypdf import PdfReader
            pages = len(PdfReader(store.resolve(pdf_path)).pages)
            checks["final_page_count"] = "passed" if pages <= 10 and report.get("page_count") == pages else "failed"
            if checks["final_page_count"] == "failed":
                issues.append(issue("FINAL_PAGE_COUNT", "Actual PDF page count exceeds 10 or differs from declared count", "writer"))
        except (ImportError, OSError, ValueError):
            manual.append("Final PDF page count could not be verified")
    elif config.get("require_final_pdf", False):
        manual.append("Final PDF required but not yet exported")
    return issues, checks, manual
