"""In-memory repository for fast service tests. Behaves like the Postgres one."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace

from server.db.seed_data import SeedData
from server.records import (
    PENDING_REVIEW,
    CatalogItem,
    Employee,
    NewTicket,
    ReviewTicket,
    RolePolicy,
)
from server.repositories.base import format_ticket_id, normalize_name, to_utc


class InMemoryRepository:
    def __init__(
        self,
        employees: Iterable[Employee],
        policies: Iterable[RolePolicy],
        catalog: Iterable[CatalogItem],
    ) -> None:
        self._employees = {
            e.employee_id: replace(
                e, equipment=tuple(sorted(e.equipment, key=lambda a: (a.acquired_on, a.item)))
            )
            for e in employees
        }
        self._policies: dict[str, list[RolePolicy]] = defaultdict(list)
        for policy in sorted(policies, key=lambda p: p.item):
            self._policies[policy.role].append(policy)
        self._catalog: dict[str, CatalogItem] = {}
        for item in catalog:
            normalized = replace(item, aliases=tuple(sorted(item.aliases)))
            for key in (item.item, *item.aliases):
                self._catalog[normalize_name(key)] = normalized
        self._tickets: list[ReviewTicket] = []

    @classmethod
    def from_seed(cls, data: SeedData) -> "InMemoryRepository":
        return cls(data.employees, data.policies, data.catalog)

    def get_employee(self, employee_id: str) -> Employee | None:
        return self._employees.get(employee_id)

    def get_role_policies(self, role: str) -> list[RolePolicy]:
        return list(self._policies.get(role, []))

    def find_catalog_item(self, name: str) -> CatalogItem | None:
        return self._catalog.get(normalize_name(name))

    def get_or_create_open_ticket(self, new: NewTicket) -> tuple[ReviewTicket, bool]:
        created_at = to_utc(new.created_at)
        for ticket in self._tickets:
            if (
                ticket.employee_id == new.employee_id
                and ticket.request_key == new.request_key
                and ticket.status == PENDING_REVIEW
            ):
                return ticket, False
        ticket = ReviewTicket(
            ticket_id=format_ticket_id(len(self._tickets) + 1),
            employee_id=new.employee_id,
            request_text=new.request_text,
            request_key=new.request_key,
            escalation_code=new.escalation_code,
            reason=new.reason,
            status=PENDING_REVIEW,
            created_at=created_at,
        )
        self._tickets.append(ticket)
        return ticket, True
