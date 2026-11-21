# Vendored from the member A archive (Data/Reviewer contract v0.1).
# Imports rewritten to integration.member_a so this tree does not collide with legacy root tools/.
"""Exact scaling and safe subtraction of additive, compatible durations only."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from integration.member_a.tools.member_a_common import stable_id

ADDITIVE = {"revenue", "cost_of_revenue", "operating_income", "net_income_attributable_to_parent",
            "research_and_development", "operating_cash_flow", "capex_cash_outflow"}


def json_number(value):
    d = Decimal(str(value))
    if not d.is_finite():
        raise ValueError("non-finite number")
    return int(d) if d == d.to_integral_value() else float(d)


def scale_amount(value, multiplier=1):
    if value is None:
        return None
    return json_number(Decimal(str(value)) * Decimal(str(multiplier)))


def subtract_cumulative(later, earlier):
    if later["name"] not in ADDITIVE or later["name"] != earlier["name"]:
        raise ValueError("Only additive flow metrics can be differenced")
    for field in ["currency", "unit", "period_start", "accounting_standard", "reporting_basis", "scope"]:
        if later.get(field) != earlier.get(field):
            raise ValueError(f"Incompatible cumulative metrics: {field}")
    if later.get("company_id") != earlier.get("company_id") or later["value_type"] != "duration" or earlier["value_type"] != "duration":
        raise ValueError("Company and duration context must match")
    start = date.fromisoformat(earlier["period_end"]) + timedelta(days=1)
    end = date.fromisoformat(later["period_end"])
    if not 60 <= (end - start).days + 1 <= 110:
        raise ValueError("Difference is not a fiscal quarter")
    result = dict(later)
    result.update(metric_id=stable_id("D", later["metric_id"], earlier["metric_id"]),
                  value=json_number(Decimal(str(later["value"])) - Decimal(str(earlier["value"]))),
                  period_start=start.isoformat(), period_type="quarter", origin="normalized",
                  original_value=None, original_unit=None, scale_multiplier=1,
                  input_metric_ids=[later["metric_id"], earlier["metric_id"]])
    derivation = dict(output_metric_id=result["metric_id"],
                      input_metric_ids=result["input_metric_ids"], operation="subtract_cumulative",
                      formula="later - earlier", period_start=start.isoformat(), period_end=end.isoformat())
    return result, derivation
