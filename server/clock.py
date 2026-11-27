"""Injected clock (PLAN.md D6): real date in the app, fixed date in tests."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol


class Clock(Protocol):
    def today(self) -> date: ...


class SystemClock:
    def today(self) -> date:
        return date.today()


@dataclass(frozen=True)
class FixedClock:
    day: date

    def today(self) -> date:
        return self.day
