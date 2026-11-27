# Vendored from the member B archive (Analysis/Writer).
# Imports rewritten to integration.member_b so this tree does not collide with legacy root tools/ or member A.
# -*- coding: utf-8 -*-
"""估值工具（按 A 的 reviewer 契约对齐）。

A 的 evaluate_formula 只认 metric_id / assumption_id 作变量名，
所以收盘价通过一条显式 assumption 传入，EPS 用 A 的 eps_diluted。
unit 必须是 ratio（A 对 display_unit=multiple 的维度要求），
分母缺失或非正时 value=null + calculation_status=not_applicable。
"""
from .formatting import q, prefix_from_bundle, rounding_policy


EPS_NAMES = {"eps_diluted", "eps_ttm", "trailing_eps"}


def compute_valuation_metrics(data_bundle, price_stats, config=None):
    config = config or {}
    metrics = data_bundle.get("metrics", [])
    prefix = prefix_from_bundle(data_bundle["bundle_id"])
    offset = int(config.get("derived_offset", 0))
    assumption_id = f"{prefix}ASMP"
    period = {"period_start": None, "period_end": None, "period_type": "instant"}

    eps = [m for m in metrics
           if m.get("name") in EPS_NAMES and m.get("value") is not None]
    eps = sorted(eps, key=lambda m: (m.get("period_end") or "",))[-1] if eps else None
    price = price_stats.get("last_close")

    status, value = "calculated", None
    if price is None or eps is None:
        status, value = "unavailable", None
    elif float(eps["value"]) <= 0:
        status, value = "not_applicable", None
    else:
        value = price / float(eps["value"])

    formula = f"{assumption_id} / {eps['metric_id']}" if eps else ""
    inputs = [eps["metric_id"]] if eps else []
    metric = {
        "metric_id": f"{prefix}D{offset + 1:03d}",
        "name": "pe_ratio",
        "value": q(value, 4) if value is not None else None,
        "unit": "ratio",  # A 要求：display_unit=multiple 时 metric unit 必须是 ratio
        "display_unit": "multiple",  # 但展示成倍数，不 ×100
        "currency": None,
        "period": period,
        "formula_id": "pe",
        "formula": formula,
        "input_metric_ids": inputs,
        "assumption_ids": [assumption_id] if eps else [],
        "rounding_policy": rounding_policy(4),
        "calculation_status": status,
        "eps_period_type": (eps or {}).get("period_type"),
        "eps_period_end": (eps or {}).get("period_end"),
        "price_date": price_stats.get("coverage_end"),
    }
    assumptions = [{
        "assumption_id": assumption_id,
        "statement": "收盘价取自研究截止时点前的行情文件，仅作为估值输入",
        "basis": "market_close_at_cutoff",
        "value": q(price, 4) if price is not None else None,
        "unit": "currency",
    }]
    limitations = [] if status == "calculated" else [
        "市盈率因缺少有效收盘价或非正每股收益，未计算"
    ]
    return {"derived_metrics": [metric], "assumptions": assumptions,
            "limitations": limitations}
