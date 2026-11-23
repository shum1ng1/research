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


METRIC_LABELS = {
    "revenue_growth_yoy": "revenue growth",
    "gross_margin": "gross margin",
    "operating_margin": "operating margin",
    "net_margin": "net margin",
    "free_cash_flow": "free cash flow",
    "fcf_margin": "free-cash-flow margin",
    "price_return_window": "price return",
    "price_volatility_window": "annualized volatility",
    "pe_ratio": "price-to-earnings ratio",
}


def metric_label(name):
    return METRIC_LABELS.get(name, str(name or "").replace("_", " "))


def money_scale(value, precision=1):
    """Return (reviewer number, display unit, prose such as '$18.6 billion')."""
    amount = Decimal(str(value))
    sign = "-" if amount < 0 else ""
    magnitude = abs(amount)
    if magnitude >= Decimal("1000000000"):
        unit, divisor, word = "usd billion", Decimal("1000000000"), "billion"
    elif magnitude >= Decimal("1000000"):
        unit, divisor, word = "usd million", Decimal("1000000"), "million"
    else:
        unit, divisor, word = "usd", Decimal(1), ""
    scaled = (magnitude / divisor).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
    number_text = f"{sign}{scaled:.{precision}f}"
    prose = f"{sign}${scaled:.{precision}f}" + (f" {word}" if word else "")
    return number_text, unit, prose


def present_number(value, unit, precision=1, sign=False, display_unit=None):
    """Return reviewer display_text, display_unit, and the prose that contains it."""
    if value is None:
        return None, None, "data unavailable"
    effective = display_unit or unit
    if effective == "currency" or unit == "currency":
        return money_scale(value, precision)
    out = _scaled(value, "multiple" if effective == "multiple" else effective, precision, sign)
    if effective == "ratio":
        return out + "%", "%", out + "%"
    if effective == "pct_point":
        return out, "pp", out + " pp"
    if effective == "multiple":
        return out, "multiple", out
    return out, effective, out


def display_string(value, unit, precision=1, sign=False, unavailable="data unavailable",
                   display_unit=None):
    """Prose form of a calculated number. Ratios stay percentages; money uses billions or millions."""
    if value is None:
        return unavailable
    _number, _unit, prose = present_number(value, unit, precision, sign, display_unit)
    return prose


def display_text(value, unit, precision=1, sign=False, display_unit=None):
    """Reviewer display_text: the numeric token that must appear inside the prose."""
    if value is None:
        return None
    number_text, _unit, _prose = present_number(value, unit, precision, sign, display_unit)
    return number_text


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
