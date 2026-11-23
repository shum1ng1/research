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
from integration.member_b.tools.formatting import display_string, json_safe, prefix_from_bundle

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


def _statement_for(name, value, unit, display_unit=None):
    """正文数字与 numeric_mention 共用 display_string，逐字一致。"""
    shown = display_string(value, unit, display_unit=display_unit)
    if name == "pe_ratio":
        return f"按最新收盘价与每股收益计算的市盈率约 {shown} 倍，不代表估值判断。"
    templates = {
        "revenue_growth_yoy": f"营收同比增长 {shown}。",
        "gross_margin": f"当季毛利率 {shown}。",
        "net_margin": f"当季净利率 {shown}。",
        "free_cash_flow": f"当期自由现金流 {shown} USD。",
        "fcf_margin": f"自由现金流利润率 {shown}。",
        "price_return_window": f"近期价格变动 {display_string(value, unit, sign=True)}。",
        "price_volatility_window": f"近期年化波动率 {shown}。",
    }
    return templates.get(name, f"{name} 为 {shown}。")


def _infer_claim_type(name):
    return "analyst_inference" if name.startswith(("price_", "pe_")) else "reported_fact"


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
            if d["name"] in ("price_volatility_window", "price_return_window"):
                findings.append({
                    "claim_id": f"{prefix}C{len(findings) + 1:03d}",
                    "statement": "行情指标数据不可用（data unavailable），未做估算。",
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
            "statement": _statement_for(d["name"], d["value"], d["unit"],
                                        d.get("display_unit")),
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
            "statement": "管理层指引：" + (ev.get("quote") or "").replace("\n", " ").strip(),
            "claim_type": "management_guidance",
            "metric_ids": [],
            "evidence_ids": [ev["evidence_id"]],
            "assumption_ids": [],
        })

    # 情景假设必须挂到一条真实可用的指标上，否则 A 的 Reviewer 会以
    # "Supported claim has neither evidence nor metrics" 拒绝（已由 e2e 暴露）。
    # 优先毛利率；毛利率不可用时退到任一可用的比率型指标，绝不生成无依据结论。
    basis = next((d for d in derived if d["name"] == "gross_margin"
                  and d.get("value") is not None and d.get("unit") == "ratio"), None)
    if basis is None:
        basis = next((d for d in derived if d.get("value") is not None
                      and d.get("unit") == "ratio"), None)
    assumption_id, scenario_shown = f"{prefix}ASM001", "当前水平"
    if basis is not None:
        scenario_shown = display_string(basis["value"], "ratio")
        label = "毛利率" if basis["name"] == "gross_margin" else "利润率"
        findings.append({
            "claim_id": f"{prefix}C{len(findings) + 1:03d}",
            "statement": f"情景假设：若{label}维持 {scenario_shown}，"
                         "在该假设下利润率区间不变；此非已实现业绩。",
            "claim_type": "scenario_assumption",
            "metric_ids": [basis["metric_id"]],
            "evidence_ids": [],
            "assumption_ids": [assumption_id],
        })
    # 连一个可用比率都没有时不生成情景 claim：A 的 Reviewer 要求每条 claim
    # 至少有 evidence 或 metric，无依据结论一律拒绝；此时由 limitations 说明。
    return findings, assumption_id, scenario_shown


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
    findings, scenario_assumption_id, scenario_shown = _build_findings(
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
        "statement": f"毛利率维持 {scenario_shown} 水平",
        "basis": "analyst_assumption",
        "value": next((d["value"] for d in derived if d["name"] == "gross_margin"), None),
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
            "risk_notes": ["情景结果不是已实现业绩"],
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
