"""Injected clock (PLAN.md D6): real date in the app, fixed date in tests."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Protocol


class Clock(Protocol):
    def today(self) -> date: ...

    def now(self) -> datetime:
        """The current moment, time-zone aware (UTC)."""
        ...


class SystemClock:
    def today(self) -> date:
        """The UTC date, so it always agrees with now()."""
        return self.now().date()

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class FixedClock:
    day: date

    def today(self) -> date:
        return self.day

    def now(self) -> datetime:
        return datetime.combine(self.day, time(), tzinfo=UTC)
