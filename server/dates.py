"""Calendar arithmetic used by the policy rules (docs/requirements.md, "Policy")."""

import calendar
from datetime import date


def add_months(day: date, months: int) -> date:
    """Shift by whole calendar months, clamping to the last day of a shorter month."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))
