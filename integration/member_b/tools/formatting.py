# Vendored from the member B archive (Analysis/Writer).
# Imports rewritten to integration.member_b so this tree does not collide with legacy root tools/ or member A.
# -*- coding: utf-8 -*-
"""共用格式化与 ID 工具（已按组员 A 的 schemas/member_a.py 对齐）。

关键点：
- rounding_policy 必须是 {"decimal_places": N} 对象，不是字符串。
- numeric_mention 用 display_text（纯数字字符串）+ display_unit（"%"/"multiple"/...）。
- A 的 reviewer 要求 display_text 逐字出现在 claim["text"] 中，
  所以 Analysis 写 statement 和 Writer 写 mention 必须共用这里的函数。
"""
from decimal import Decimal, ROUND_HALF_UP


def q(value, digits):
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def rounded(value, precision):
    """返回 Decimal，供 create_mention 判断精确展示值。"""
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-precision),
                                        rounding=ROUND_HALF_UP)


def _scaled(value, unit, precision, sign=False):
    quantum = Decimal(1).scaleb(-precision)
    d = Decimal(str(value))
    if unit == "ratio":
        d = d * 100
    out = f"{d.quantize(quantum, rounding=ROUND_HALF_UP):.{precision}f}"
    return ("+" if sign and d >= 0 else "") + out


def display_string(value, unit, precision=1, sign=False, unavailable="data unavailable",
                   display_unit=None):
    """给报告正文用（中文句子里的数字）。

    display_unit 用于「metric 的 unit=ratio（A 要求），但要展示成倍数」的情况：
    如市盈率 40.4 倍，不能按 ratio ×100 显示成 4040.0%。
    """
    if value is None:
        return unavailable
    effective = display_unit or unit
    out = _scaled(value, effective if effective != "multiple" else "multiple",
                  precision, sign)
    if effective == "ratio":
        return out + "%"
    if effective == "pct_point":
        return out + " pp"
    return out


def display_text(value, unit, precision=1, sign=False, display_unit=None):
    """给 A 的 numeric_mention.display_text 用：纯数字字符串。

    A 的 reviewer 会 strip 逗号/$ 并 rstrip('%') 后按 precision 精确比对。
    display_unit="multiple" 时不 ×100（市盈率等倍数）。
    """
    if value is None:
        return None
    effective = display_unit or unit
    out = _scaled(value, effective if effective != "multiple" else "multiple",
                  precision, sign)
    return out + "%" if effective == "ratio" else out


def a_display_unit(unit, currency=None):
    """A 的 verify_numeric_claims.factors 认识的展示单位。"""
    if unit == "ratio":
        return "%"
    if unit == "pct_point":
        return "pp"
    if unit == "multiple":
        return "multiple"
    if unit == "currency":
        return currency or "USD"
    if unit == "currency_per_share":
        return "USD/share"
    if unit == "count":
        return "shares"
    return unit


def rounding_policy(precision):
    """A 的 schema/reviewer 要求 dict。"""
    return {"decimal_places": precision}


def prefix_from_bundle(bundle_id):
    """DEMO_MSFT_B001 -> DEMO_MSFT_；A 的 bundle_xxx -> bundle_。"""
    return bundle_id.rsplit("_", 1)[0] + "_"


def next_id(prefix, existing, width=3):
    used = set(existing or [])
    n = 1
    while f"{prefix}{n:0{width}d}" in used:
        n += 1
    return f"{prefix}{n:0{width}d}"


def json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float):
        import math
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value
