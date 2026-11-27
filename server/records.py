"""Plain data records passed between repositories and services.

Services depend on these, never on SQLAlchemy models, so they can be tested
against the in-memory repository.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from server.codes import EscalationCode

PENDING_REVIEW: Final = "PENDING_REVIEW"


@dataclass(frozen=True)
class EquipmentItem:
    item: str
    acquired_on: date


@dataclass(frozen=True)
class Employee:
    employee_id: str
    name: str
    role: str
    hire_date: date
    equipment: tuple[EquipmentItem, ...] = ()
    """Oldest first."""


@dataclass(frozen=True)
class RolePolicy:
    role: str
    item: str
    entitled: bool
    refresh_months: int | None
    """None when the role is not entitled to the item."""
    min_tenure_months: int = 0


@dataclass(frozen=True)
class CatalogItem:
    item: str
    unit_cost_usd: int
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class NewTicket:
    employee_id: str
    request_text: str
    request_key: str
    """Identifies "the same request" for idempotency (computed by the service)."""
    escalation_code: EscalationCode
    reason: str
    created_at: datetime


@dataclass(frozen=True)
class ReviewTicket:
    ticket_id: str
    employee_id: str
    request_text: str
    request_key: str
    escalation_code: EscalationCode
    reason: str
    status: str
    created_at: datetime
