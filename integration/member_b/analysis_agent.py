# Vendored from the member B archive (Analysis/Writer).
# Imports rewritten to integration.member_b so this tree does not collide with legacy root tools/ or member A.
# -*- coding: utf-8 -*-
"""Analysis Agent（B 负责的第一个 Agent），已按 A 的契约对齐。

契约：analysis_agent.run(request, config, data_bundle, feedback=None) -> dict
- 返回 A 7.2 统一外壳；payload 才是 AnalysisBundle。
- 数字只来自 DataBundle 或 tools/ 纯函数；不联网、不调 yfinance/SEC。
- 公式用 A 的 metric_id 作变量名，rounding_policy 用 dict。
- 正文 statement 不含未登记数字，供 A 的 verify_numeric_claims 逐条核对。

LLM 接入点见 _polish_with_llm()：默认关闭；开启后只允许按 claim_id 覆盖
statement，不得改数字、ID、claim_type、metric_ids、evidence_ids。
"""
from integration.member_b.tools import finance_metrics, price_metrics, valuation
from integration.member_b.tools.formatting import display_string, json_safe, metric_label, prefix_from_bundle

SCHEMA_VERSION = "0.1"


def _envelope(run_id, stage, status, payload, warnings=None, errors=None,
              artifacts=None, config=None):
    config = config or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "stage": stage,
        "status": status,
        "payload": json_safe(payload),
        "warnings": warnings or [],
        "errors": errors or [],
        "artifacts": artifacts or [],
        "trace_path": config.get("trace_path") or f"runs/{run_id}/logs/{stage}_tools.jsonl",
    }


def _validate(request, data_bundle):
    errors = []
    if request.get("cutoff_at") and data_bundle.get("cutoff_at") \
            and request["cutoff_at"] != data_bundle["cutoff_at"]:
        errors.append("INVALID_REQUEST: 请求与数据包的 cutoff_at 不一致")
    if not data_bundle.get("bundle_id"):
        errors.append("INVALID_BUNDLE: 缺 bundle_id")
    if not isinstance(data_bundle.get("metrics"), list):
        errors.append("INVALID_BUNDLE: metrics 不是数组")
    return errors


_MARGIN_SCENARIO = (
    ("gross_margin", "gross margin"),
    ("net_margin", "net margin"),
    ("fcf_margin", "free-cash-flow margin"),
)


def _statement_for(metric):
    """正文数字与 numeric_mention 共用 display_string，逐字一致。"""
    name = metric["name"]
    label = metric_label(name)
    shown = display_string(metric["value"], metric["unit"],
                           sign=name.startswith("price_return"),
                           display_unit=metric.get("display_unit"))
    if name == "pe_ratio":
        period = metric.get("eps_period_type") or ""
        if period == "quarter":
            eps_words = "quarterly earnings per share. It is not an annualized multiple"
        elif period in {"year", "annual"}:
            eps_words = "annual earnings per share"
        elif period == "ttm":
            eps_words = "trailing-twelve-month earnings per share"
        else:
            eps_words = "the latest available earnings per share. It is not annualized unless that period is already a full year"
        return (f"The price-to-earnings ratio is calculated from the latest close divided by {eps_words}. "
                f"The price-to-earnings ratio is {shown}. This is not a valuation opinion.")
    if name == "free_cash_flow":
        return (f"Free cash flow is calculated from operating cash flow minus capital expenditure. "
                f"Free cash flow is {shown}. It is not a line item copied from the filing.")
    if name == "fcf_margin":
        return (f"Free-cash-flow margin is calculated from free cash flow and revenue. "
                f"Free-cash-flow margin is {shown}. It is not a line item copied from the filing.")
    if name == "revenue_growth_yoy":
        return (f"Revenue growth is calculated from the latest two revenue periods. "
                f"Revenue growth is {shown}.")
    if name == "gross_margin":
        return (f"Gross margin is calculated from gross profit and revenue for the same period. "
                f"Gross margin is {shown}.")
    if name == "net_margin":
        return (f"Net margin is calculated from net income and revenue for the same period. "
                f"Net margin is {shown}.")
    if name == "price_return_window":
        return (f"The price return is calculated from closes at or before the cutoff. "
                f"The price return is {shown}.")
    if name == "price_volatility_window":
        return f"Annualized volatility is calculated as {shown}."
    return f"{label} is calculated as {shown}."


def _infer_claim_type(name):
    if name.startswith(("price_", "pe_")) or name in {
        "revenue_growth_yoy", "gross_margin", "operating_margin", "net_margin",
        "free_cash_flow", "fcf_margin",
    }:
        return "analyst_inference"
    return "reported_fact"


def _polish_with_llm(findings, config):
    """Offline no-op. Enabling this placeholder is not a model call."""
    llm = config.get("llm") or {}
    if not llm.get("enabled"):
        return findings, []
    raise RuntimeError("the polish placeholder is not a live model; the tool-calling author is required")


def _build_findings(derived, data_bundle, prefix):
    findings = []
    metrics_by_id = {m.get("metric_id"): m for m in data_bundle.get("metrics", [])}

    for d in derived:
        if d.get("calculation_status") != "calculated" or d.get("value") is None:
            if d["name"] in ("price_volatility_window", "price_return_window", "pe_ratio"):
                findings.append({
                    "claim_id": f"{prefix}C{len(findings) + 1:03d}",
                    "statement": f"The {metric_label(d['name'])} is unavailable. No estimate was made.",
                    "claim_type": "analyst_inference",
                    "metric_ids": [d["metric_id"]],
                    "evidence_ids": [],
                    "assumption_ids": [],
                })
            continue
        evidence_ids = []
        for mid in d.get("input_metric_ids", []):
            eid = (metrics_by_id.get(mid) or {}).get("evidence_id")
            if eid and eid not in evidence_ids:
                evidence_ids.append(eid)
        ctype = _infer_claim_type(d["name"])
        findings.append({
            "claim_id": f"{prefix}C{len(findings) + 1:03d}",
            "statement": _statement_for(d),
            "claim_type": ctype,
            "metric_ids": [d["metric_id"]],
            "evidence_ids": evidence_ids if ctype == "reported_fact" else [],
            "assumption_ids": [],
        })

    # 管理层指引单列；原文里的数字交由 Reviewer 检查引用支持
    for ev in data_bundle.get("evidence", []):
        quote = (ev.get("quote") or "").lower()
        if "management" not in quote and "guidance" not in quote:
            continue
        findings.append({
            "claim_id": f"{prefix}C{len(findings) + 1:03d}",
            "statement": "Management guidance: " + (ev.get("quote") or "").replace("\n", " ").strip(),
            "claim_type": "management_guidance",
            "metric_ids": [],
            "evidence_ids": [ev["evidence_id"]],
            "assumption_ids": [],
        })

    # Only a real margin can anchor the scenario. Revenue growth is not a margin.
    basis, label = None, None
    for name, pretty in _MARGIN_SCENARIO:
        found = next((d for d in derived if d["name"] == name and d.get("value") is not None
                      and d.get("unit") == "ratio"), None)
        if found is not None:
            basis, label = found, pretty
            break
    assumption_id, scenario_shown = f"{prefix}ASM001", "the current level"
    if basis is not None:
        scenario_shown = display_string(basis["value"], "ratio")
        findings.append({
            "claim_id": f"{prefix}C{len(findings) + 1:03d}",
            "statement": f"Scenario assumption: if {label} stays at {scenario_shown}, "
                         "that margin is held constant. This is not a reported result.",
            "claim_type": "scenario_assumption",
            "metric_ids": [basis["metric_id"]],
            "evidence_ids": [],
            "assumption_ids": [assumption_id],
        })
    # 连一个可用比率都没有时不生成情景 claim：A 的 Reviewer 要求每条 claim
    # 至少有 evidence 或 metric，无依据结论一律拒绝；此时由 limitations 说明。
    return findings, assumption_id, scenario_shown, label if basis is not None else None, (
        basis["value"] if basis is not None else None)


def _revision(feedback):
    if not feedback:
        return 1
    try:
        return int(feedback.get("base_revision", 0) or 0) + 1
    except (TypeError, ValueError):
        return 1


def run(request, config, data_bundle, feedback=None):
    config = config or {}
    request = request or {}
    errors = _validate(request, data_bundle)
    if errors:
        return _envelope(request.get("run_id", "unknown"), "analysis", "failed",
                         {}, errors=errors, config=config)

    prefix = prefix_from_bundle(data_bundle["bundle_id"])
    warnings = []

    fin = finance_metrics.compute_financial_metrics(data_bundle, config)
    derived = list(fin["derived_metrics"])

    price_cfg = dict(config, derived_offset=len(derived))
    price = price_metrics.compute_price_metrics(data_bundle, price_cfg)
    derived.extend(price["derived_metrics"])

    val_cfg = dict(config, derived_offset=len(derived))
    val = valuation.compute_valuation_metrics(data_bundle, price["price_stats"], val_cfg)
    derived.extend(val["derived_metrics"])

    limitations = fin["limitations"] + price["limitations"] + val["limitations"]
    findings, scenario_assumption_id, scenario_shown, scenario_label, scenario_value = _build_findings(
        derived, data_bundle, prefix)
    if (config.get("llm") or {}).get("enabled"):
        from integration.llm_author import author_statements
        from integration.member_a.tools.agent_llm import LLMError
        try:
            findings = author_statements(findings, derived, data_bundle, config)
        except LLMError as exc:
            return _envelope(request.get("run_id", "unknown"), "analysis", "failed", {},
                             errors=[{"code": "LLM_ERROR", "message": str(exc), "retryable": False}],
                             config=config)
        warnings.append("分析句子由模型按工具结果改写；数字仍只来自 Python 计算，失败时不会退回模板句。")
        if getattr(author_statements, "limit_note", ""):
            warnings.append(author_statements.limit_note)
    else:
        findings, llm_warnings = _polish_with_llm(findings, config)
        warnings.extend(llm_warnings)

    if feedback and feedback.get("target_agent") not in (None, "analysis"):
        warnings.append("反馈指定对象不属于 B 的分析阶段，未做返工改动")

    partial = any(d["name"] == "price_volatility_window"
                  and d.get("calculation_status") != "calculated" for d in derived)
    assumptions = list(price.get("assumptions") or []) + list(val["assumptions"]) + [{
        "assumption_id": scenario_assumption_id,
        "statement": (f"{scenario_label} is assumed to remain at {scenario_shown}."
                      if scenario_label else "No margin scenario was calculated."),
        "basis": "analyst_assumption",
        "value": scenario_value,
        "unit": "ratio",
    }]

    payload = {
        "analysis_id": f"{prefix}A001",
        "revision": _revision(feedback),
        "input_bundle_id": data_bundle["bundle_id"],
        "input_bundle_revision": data_bundle.get("revision", 1),
        "cutoff_at": data_bundle.get("cutoff_at"),
        "derived_metrics": derived,
        "findings": findings,
        "assumptions": assumptions,
        "scenarios": [{
            "scenario_id": f"{prefix}SC001",
            "name": "base",
            "inputs": [f["claim_id"] for f in findings
                       if f["claim_type"] == "scenario_assumption"],
            "outputs": [],
            "risk_notes": ["The scenario result is not a reported result."],
        }],
        "risks": [{"risk_id": f"{prefix}RK{i + 1:03d}",
                   "statement": note, "evidence_ids": []}
                  for i, note in enumerate(limitations)],
        "limitations": limitations,
        "warnings": warnings,
    }
    return _envelope(request.get("run_id", "unknown"), "analysis",
                     "partial" if partial else "success", payload,
                     warnings=warnings, config=config)
