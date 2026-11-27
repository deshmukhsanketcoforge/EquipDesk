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


def whole_months_between(start: date, end: date) -> int:
    """Whole calendar months from `start` to `end`, counted from `start`.

    The inverse of add_months: whole_months_between(d, add_months(d, n)) == n.
    Meant for start <= end (tenure, cooldown). When `end` is earlier the result is
    negative, but because add_months clamps month ends it is not always the negated
    forward count: (Jan 31 -> Feb 28) is 1, while (Feb 28 -> Jan 31) is 0.
    """
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if months > 0 and add_months(start, months) > end:
        months -= 1
    elif months < 0 and add_months(start, months) < end:
        months += 1
    return months
