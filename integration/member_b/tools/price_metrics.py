# Vendored from the member B archive (Analysis/Writer).
# Imports rewritten to integration.member_b so this tree does not collide with legacy root tools/ or member A.
# -*- coding: utf-8 -*-
"""行情计算。纳入窗口只比较带时区的收盘时刻与 cutoff，不用日期字符串切片。

年化波动率含 log/sqrt，v0.1 公式校验器无法重算。这里不填数值，避免
uncertain 被当成已审核通过。收益率用两条显式收盘价假设，公式可被重算。
"""
import csv
import os
from datetime import datetime

from .formatting import q, prefix_from_bundle, rounding_policy


def _resolve(path, data_root):
    # Relative artifact paths are rooted at the run's data_root, never the shell cwd.
    if os.path.isabs(path):
        return path
    return os.path.join(data_root or ".", path)


def _aware(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def read_price_rows(price_file, cutoff, data_root):
    """Return (rows, skipped_without_close_timestamp).

    rows are (session_date, close, close_at) with close_at <= cutoff.
    A row whose session_date equals the cutoff calendar date is excluded when
    its exchange close is still after cutoff.
    """
    full = _resolve(price_file["path"], data_root)
    rows = []
    skipped = 0
    with open(full, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            close_at = _aware(row.get("bar_close_at_utc"))
            session = (row.get("session_date") or row.get("date") or "")[:10]
            if close_at is None:
                skipped += 1
                continue
            if cutoff is not None and close_at > cutoff:
                continue
            try:
                rows.append((session, float(row["close"]), close_at))
            except (TypeError, ValueError, KeyError):
                skipped += 1
    rows.sort(key=lambda item: item[2])
    return rows, skipped


def compute_price_metrics(data_bundle, config=None):
    config = config or {}
    prefix = prefix_from_bundle(data_bundle["bundle_id"])
    offset = int(config.get("derived_offset", 0))
    cutoff = _aware(data_bundle.get("cutoff_at"))
    data_root = config.get("data_root", ".")
    derived, limitations, assumptions = [], [], []
    stats = {"last_close": None, "observations": 0,
             "coverage_start": None, "coverage_end": None}

    def empty(reason):
        limitations.append(reason)
        return {"derived_metrics": derived, "price_stats": stats,
                "assumptions": assumptions, "limitations": limitations}

    files = data_bundle.get("price_files") or []
    if cutoff is None:
        return empty("cutoff_at 缺少时区，行情未计算，避免用日历日期代替收盘时刻")
    if not files:
        return empty("缺少行情文件，收益率与波动率未计算")

    price_file = files[0]
    rows, skipped = read_price_rows(price_file, cutoff, data_root)
    if skipped:
        limitations.append("部分行情行没有带时区的 bar_close_at_utc，已排除，不用 session_date 代替")
    stats["observations"] = len(rows)
    if rows:
        stats["coverage_start"], stats["coverage_end"] = rows[0][0], rows[-1][0]
        stats["last_close"] = rows[-1][1]

    def add(name, value, formula, formula_id, start, end, status="calculated", assumption_ids=None):
        derived.append({
            "metric_id": f"{prefix}D{offset + len(derived) + 1:03d}",
            "name": name,
            "value": q(value, 4) if value is not None else None,
            "unit": "ratio",
            "currency": None,
            "period": {"period_start": start, "period_end": end,
                       "period_type": "market_window"},
            "formula_id": formula_id,
            "formula": formula,
            "input_metric_ids": [],
            "assumption_ids": list(assumption_ids or []),
            "rounding_policy": rounding_policy(4),
            "calculation_status": status,
            "price_file": price_file.get("path"),
        })

    if len(rows) >= 2:
        first_id, last_id = f"{prefix}PXFIRST", f"{prefix}PXLAST"
        first_px, last_px = q(rows[0][1], 4), q(rows[-1][1], 4)
        assumptions.extend([
            {"assumption_id": first_id, "statement": "窗口第一根已收盘价",
             "basis": "bar_close_at_or_before_cutoff", "value": first_px, "unit": "currency"},
            {"assumption_id": last_id, "statement": "窗口最后一根已收盘价",
             "basis": "bar_close_at_or_before_cutoff", "value": last_px, "unit": "currency"},
        ])
        value = None if not first_px else (last_px / first_px) - 1
        add("price_return_window", value, f"({last_id} / {first_id}) - 1",
            "price_return_window", rows[0][0], rows[-1][0],
            "calculated" if value is not None else "unavailable",
            [first_id, last_id])
    else:
        add("price_return_window", None, "", "price_return_window",
            stats["coverage_start"], stats["coverage_end"], "unavailable")
        limitations.append("截止时刻前不足两根已收盘 K 线，收益率未计算")

    # Withhold the number. Publishing log/sqrt output marks the metric uncertain
    # and the v0.1 reviewer then routes the run to manual_review.
    add("price_volatility_window", None, "", "price_volatility_window",
        stats["coverage_start"], stats["coverage_end"], "unavailable")
    limitations.append("年化波动率需要对数与平方根，当前公式校验器不能重算，因此不给出数值")
    return {"derived_metrics": derived, "price_stats": stats,
            "assumptions": assumptions, "limitations": limitations}
