# Vendored from the member B archive (Analysis/Writer).
# Imports rewritten to integration.member_b so this tree does not collide with legacy root tools/ or member A.
# -*- coding: utf-8 -*-
"""财务计算工具（已按 A 的 reviewer 契约对齐）。

关键改动：
- period 用扁平 period_start/period_end/period_type；
- rounding_policy 是 {"decimal_places": 4}；
- formula 用 A 的 metric_id 作变量名，因为 A 的 evaluate_formula
  只认 metric_id / assumption_id，不认识 revenue_t 这类别名；
- calculation_status 用 calculated / unavailable / not_applicable；
- 指标名兼容 A 的字段：capex_cash_outflow、net_income_attributable_to_parent。
"""
from .formatting import q, prefix_from_bundle, rounding_policy

NAME_ALIASES = {
    "capital_expenditure": ("capex_cash_outflow",),
    "net_income": ("net_income_attributable_to_parent",),
    "gross_profit": ("gross_profit",),
    "revenue": ("revenue",),
    "operating_cash_flow": ("operating_cash_flow",),
}


def _period_of(metric):
    return {
        "period_start": metric.get("period_start"),
        "period_end": metric.get("period_end"),
        "period_type": metric.get("period_type"),
    }


EMPTY_PERIOD = {"period_start": None, "period_end": None, "period_type": None}


def _by_name(metrics, canonical):
    names = NAME_ALIASES.get(canonical, (canonical,))
    arr = [m for m in metrics
           if m.get("name") in names and m.get("value") is not None]
    arr.sort(key=lambda m: (m.get("period_end") or "", m.get("period_start") or ""))
    return arr


def check_consistency(metrics):
    """口径混用在计算阶段拦截；文案不含数字，避免报告被判未登记数字。"""
    issues = []
    grouped = {}
    for m in metrics:
        grouped.setdefault(m.get("name"), []).append(m)
    for name, arr in grouped.items():
        for key in ("period_type", "accounting_standard", "reporting_basis", "currency"):
        # 仅当字段实际存在且取值不一致时才拦截
            values = {m.get(key) for m in arr if m.get(key) is not None}
            if len(values) > 1:
                issues.append(f"{name} 的 {key} 口径混用，已在计算阶段拦截")
    return issues


def compute_financial_metrics(data_bundle, config=None):
    config = config or {}
    metrics = data_bundle.get("metrics", [])
    prefix = prefix_from_bundle(data_bundle["bundle_id"])
    derived = []
    limitations = check_consistency(metrics)

    def add(name, value, period, formula_id, formula, inputs,
            unit="ratio", currency=None, status="calculated", note=None):
        derived.append({
            "metric_id": f"{prefix}D{len(derived) + 1:03d}",
            "name": name,
            "value": q(value, 4) if value is not None else None,
            "unit": unit,
            "currency": currency,
            "period": period,
            "formula_id": formula_id,
            "formula": formula,
            "input_metric_ids": list(inputs),
            "assumption_ids": [],
            "rounding_policy": rounding_policy(4),
            "calculation_status": status,
        })
        if status != "calculated" and note:
            limitations.append(note)

    revenue = _by_name(metrics, "revenue")
    latest_rev = revenue[-1] if revenue else None

    # 1) 营收同比：公式用 metric_id 变量名
    if len(revenue) >= 2:
        cur, prev = revenue[-1], revenue[-2]
        c, p = cur["metric_id"], prev["metric_id"]
        denominator = abs(prev["value"])
        value = (cur["value"] - prev["value"]) / denominator if denominator else None
        add("revenue_growth_yoy", value, _period_of(cur), "revenue_growth_yoy",
            f"({c} - {p}) / abs({p})", [c, p])
    else:
        add("revenue_growth_yoy", None, EMPTY_PERIOD, "revenue_growth_yoy",
            "", [], status="unavailable",
            note="营收增长缺少至少两期营收数据，未计算")

    # 2) 毛利率 / 净利率：分子分母必须同期间
    for name, input_name in (("gross_margin", "gross_profit"),
                             ("net_margin", "net_income")):
        candidates = _by_name(metrics, input_name)
        same = [m for m in candidates
                if latest_rev and m.get("period_end") == latest_rev.get("period_end")]
        if latest_rev and same and latest_rev["value"]:
            numerator, denominator = same[-1], latest_rev
            add(name, numerator["value"] / denominator["value"],
                _period_of(latest_rev), name,
                f"{numerator['metric_id']} / {denominator['metric_id']}",
                [numerator["metric_id"], denominator["metric_id"]])
        else:
            add(name, None, EMPTY_PERIOD, name, "", [], status="unavailable",
                note=f"{name.replace('_', ' ')} 缺少同期间数据，未计算")

    # 3) FCF 与 FCF 利润率
    ocf = _by_name(metrics, "operating_cash_flow")
    capex = _by_name(metrics, "capital_expenditure")
    fcf_value = None
    if ocf and capex and ocf[-1].get("period_end") == capex[-1].get("period_end"):
        o, x = ocf[-1], capex[-1]
        fcf_value = o["value"] - x["value"]
        add("free_cash_flow", fcf_value, _period_of(o), "free_cash_flow",
            f"{o['metric_id']} - {x['metric_id']}", [o["metric_id"], x["metric_id"]],
            unit="currency", currency=o.get("currency"))
    else:
        add("free_cash_flow", None, EMPTY_PERIOD, "free_cash_flow", "", [],
            unit="currency", status="unavailable",
            note="自由现金流缺少同期间的经营现金流与资本开支，未计算")

    if fcf_value is not None and latest_rev and latest_rev["value"]:
        o, x, r = ocf[-1], capex[-1], latest_rev
        add("fcf_margin", fcf_value / r["value"], _period_of(r), "fcf_margin",
            f"({o['metric_id']} - {x['metric_id']}) / {r['metric_id']}",
            [o["metric_id"], x["metric_id"], r["metric_id"]])
    else:
        add("fcf_margin", None, EMPTY_PERIOD, "fcf_margin", "", [],
            status="unavailable", note="自由现金流利润率缺少输入，未计算")

    return {"derived_metrics": derived, "limitations": limitations}
