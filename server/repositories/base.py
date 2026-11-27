"""The repository interface shared by the Postgres and in-memory implementations.

tests/test_repository_contract.py runs the same tests against both.
"""

from datetime import UTC, datetime
from typing import Protocol

from server.records import CatalogItem, Employee, NewTicket, ReviewTicket, RolePolicy


def normalize_name(name: str) -> str:
    """Case-insensitive, whitespace-insensitive key for catalog names and aliases."""
    return " ".join(name.split()).lower()


def to_utc(moment: datetime) -> datetime:
    """Ticket timestamps are stored and returned in UTC; naive values are a bug."""
    if moment.utcoffset() is None:
        raise ValueError("ticket created_at must have a time zone")
    return moment.astimezone(UTC)


def format_ticket_id(number: int) -> str:
    return f"TKT-{number:06d}"


class Repository(Protocol):
    def get_employee(self, employee_id: str) -> Employee | None:
        """The employee with their equipment (oldest first), or None. Exact ID match."""
        ...

    def get_role_policies(self, role: str) -> list[RolePolicy]:
        """One policy per catalog item for a known role (exact match), else empty."""
        ...

    def find_catalog_item(self, name: str) -> CatalogItem | None:
        """Look up by item name or alias, ignoring case and extra whitespace."""
        ...

    def get_or_create_open_ticket(self, new: NewTicket) -> tuple[ReviewTicket, bool]:
        """Return the open ticket for (employee, request key), creating it if needed.

        The boolean is True when a ticket was created. `created_at` must have a
        time zone (ValueError otherwise) and is returned in UTC.
        """
        ...
