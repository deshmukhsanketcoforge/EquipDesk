"""Postgres repository (SQLAlchemy 2). The caller owns the session and transaction."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.codes import EscalationCode
from server.db.models import (
    CatalogAliasRow,
    CatalogItemRow,
    EmployeeRow,
    EquipmentAssetRow,
    ReviewTicketRow,
    RolePolicyRow,
)
from server.records import (
    PENDING_REVIEW,
    CatalogItem,
    Employee,
    EquipmentItem,
    NewTicket,
    ReviewTicket,
    RolePolicy,
)
from server.repositories.base import (
    Repository,
    RepositoryProvider,
    format_ticket_id,
    normalize_name,
    to_utc,
)


class SqlRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        return self._session

    def get_employee(self, employee_id: str) -> Employee | None:
        row = self._session.get(EmployeeRow, employee_id)
        if row is None:
            return None
        equipment = self._session.execute(
            select(EquipmentAssetRow.item, EquipmentAssetRow.acquired_on)
            .where(EquipmentAssetRow.employee_id == employee_id)
            .order_by(EquipmentAssetRow.acquired_on, EquipmentAssetRow.item)
        )
        return Employee(
            employee_id=row.employee_id,
            name=row.name,
            role=row.role,
            hire_date=row.hire_date,
            equipment=tuple(EquipmentItem(item, acquired_on) for item, acquired_on in equipment),
        )

    def get_role_policies(self, role: str) -> list[RolePolicy]:
        rows = self._session.scalars(
            select(RolePolicyRow).where(RolePolicyRow.role == role).order_by(RolePolicyRow.item)
        )
        return [
            RolePolicy(
                role=r.role,
                item=r.item,
                entitled=r.entitled,
                refresh_months=r.refresh_months,
                min_tenure_months=r.min_tenure_months,
            )
            for r in rows
        ]

    def find_catalog_item(self, name: str) -> CatalogItem | None:
        key = normalize_name(name)
        if not key:
            return None
        row = self._session.get(CatalogItemRow, key)
        if row is None:
            alias = self._session.get(CatalogAliasRow, key)
            if alias is None:
                return None
            row = self._session.get_one(CatalogItemRow, alias.item)
        aliases = self._session.scalars(
            select(CatalogAliasRow.alias)
            .where(CatalogAliasRow.item == row.item)
            .order_by(CatalogAliasRow.alias)
        )
        return CatalogItem(item=row.item, unit_cost_usd=row.unit_cost_usd, aliases=tuple(aliases))

    def get_or_create_open_ticket(self, new: NewTicket) -> tuple[ReviewTicket, bool]:
        created_at = to_utc(new.created_at)
        existing = self._find_open_ticket(new.employee_id, new.request_key)
        if existing is not None:
            return _to_ticket(existing), False
        row = ReviewTicketRow(
            employee_id=new.employee_id,
            request_text=new.request_text,
            request_key=new.request_key,
            escalation_code=new.escalation_code.value,
            reason=new.reason,
            status=PENDING_REVIEW,
            created_at=created_at,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError:  # pragma: no cover - concurrent insert of the same request
            raced = self._find_open_ticket(new.employee_id, new.request_key)
            assert raced is not None
            return _to_ticket(raced), False
        return _to_ticket(row), True

    def _find_open_ticket(self, employee_id: str, request_key: str) -> ReviewTicketRow | None:
        return self._session.scalars(
            select(ReviewTicketRow).where(
                ReviewTicketRow.employee_id == employee_id,
                ReviewTicketRow.request_key == request_key,
                ReviewTicketRow.status == PENDING_REVIEW,
            )
        ).one_or_none()


def _to_ticket(row: ReviewTicketRow) -> ReviewTicket:
    return ReviewTicket(
        ticket_id=format_ticket_id(row.id),
        employee_id=row.employee_id,
        request_text=row.request_text,
        request_key=row.request_key,
        escalation_code=EscalationCode(row.escalation_code),
        reason=row.reason,
        status=row.status,
        created_at=to_utc(row.created_at),
    )


def sql_repositories(engine: Engine) -> RepositoryProvider:
    """One new session per REST request or MCP tool call, closed afterwards.

    Closing rolls back whatever is left, so a database error in one call can
    never leave a broken session behind for the next (no PendingRollbackError).
    Nothing is committed here: the read-only tools need no commit, and the
    ticket-writing tool commits explicitly (EQP-8).
    """

    @contextmanager
    def scope() -> Iterator[Repository]:
        with Session(engine) as session:
            yield SqlRepository(session)

    return scope
