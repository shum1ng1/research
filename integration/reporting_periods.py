"""Comparability rules shared by financial calculations and their reviewer."""
from datetime import date


def comparable_prior_year(current, prior):
    """Match reporting basis and calendar position, including 52/53-week years."""
    fields = ("period_type", "unit", "currency", "scope", "accounting_standard", "reporting_basis")
    if not current.get("period_type") or any(current.get(k) != prior.get(k) for k in fields):
        return False
    try:
        offsets = [(date.fromisoformat(current[k]) - date.fromisoformat(prior[k])).days
                   for k in ("period_start", "period_end")]
    except (KeyError, TypeError, ValueError):
        return False
    return all(358 <= days <= 373 for days in offsets)
