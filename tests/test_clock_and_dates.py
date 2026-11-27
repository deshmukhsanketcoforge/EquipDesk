"""EQP-3/EQP-4/EQP-8: injected clock (PLAN.md D6) and calendar-month arithmetic."""

from datetime import UTC, date, datetime, timedelta

import pytest

from server.clock import FixedClock, SystemClock
from server.dates import add_months, whole_months_between


def test_fixed_clock_returns_its_date() -> None:
    assert FixedClock(date(2026, 1, 31)).today() == date(2026, 1, 31)


def test_system_clock_returns_the_utc_date() -> None:
    # UTC, not the host's local date (date.today()), which differs near midnight
    # on machines in other time zones. Read before and after to survive midnight.
    before = datetime.now(UTC).date()
    today = SystemClock().today()
    after = datetime.now(UTC).date()
    assert today in {before, after}


def test_fixed_clock_now_is_midnight_utc_on_its_date() -> None:
    assert FixedClock(date(2026, 11, 26)).now() == datetime(2026, 11, 26, tzinfo=UTC)


def test_system_clock_today_is_the_utc_date_of_now() -> None:
    # One time zone for both, so a ticket's created_at and the eligibility "today"
    # of the same run never fall on different days.
    class LateEvening(SystemClock):
        def now(self) -> datetime:
            return datetime(2026, 11, 26, 23, 59, tzinfo=UTC)

    assert LateEvening().today() == date(2026, 11, 26)


def test_system_clock_now_is_aware_utc() -> None:
    now = SystemClock().now()
    assert now.utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2026, 1, 15), 1, date(2026, 2, 15)),
        (date(2026, 1, 15), -1, date(2025, 12, 15)),
        (date(2026, 1, 15), 36, date(2029, 1, 15)),
        (date(2026, 11, 26), -56, date(2022, 3, 26)),
        # Day clamped to the last day of a shorter month.
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2024, 2, 29), 12, date(2025, 2, 28)),
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
        (date(2026, 5, 10), 0, date(2026, 5, 10)),
    ],
)
def test_add_months(start: date, months: int, expected: date) -> None:
    assert add_months(start, months) == expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 1, 15), date(2026, 1, 15), 0),
        (date(2026, 1, 15), date(2026, 2, 14), 0),
        (date(2026, 1, 15), date(2026, 2, 15), 1),
        (date(2020, 11, 26), date(2026, 11, 26), 72),
        # Agrees with add_months clamping: Jan 31 + 1 month = Feb 28.
        (date(2026, 1, 31), date(2026, 2, 27), 0),
        (date(2026, 1, 31), date(2026, 2, 28), 1),
        (date(2024, 2, 29), date(2025, 2, 28), 12),
        # End before start counts backwards.
        (date(2026, 3, 1), date(2026, 1, 1), -2),
        (date(2026, 3, 15), date(2026, 1, 20), -1),
        # Counted from start, so the backward count is not always the negated forward one.
        (date(2026, 2, 28), date(2026, 1, 31), 0),
    ],
)
def test_whole_months_between(start: date, end: date, expected: int) -> None:
    assert whole_months_between(start, end) == expected


@pytest.mark.parametrize("months", [-30, -1, 0, 1, 3, 36])
@pytest.mark.parametrize("start", [date(2024, 1, 31), date(2026, 8, 31), date(2025, 6, 15)])
def test_whole_months_between_inverts_add_months(start: date, months: int) -> None:
    assert whole_months_between(start, add_months(start, months)) == months
