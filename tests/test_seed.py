"""EQP-3: writing the seed to Postgres."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.dates import add_months
from server.db.models import (
    CatalogAliasRow,
    CatalogItemRow,
    EmployeeRow,
    EquipmentAssetRow,
    ReviewTicketRow,
    RolePolicyRow,
)
from server.db.seed import seed

TODAY = date(2026, 11, 26)


def _rows(session: Session, *columns: Any) -> list[tuple[object, ...]]:
    return sorted((tuple(row) for row in session.execute(select(*columns))), key=repr)


def _snapshot(session: Session) -> dict[str, list[tuple[object, ...]]]:
    """Every seeded row by its natural values (surrogate ids left out)."""
    return {
        "employees": _rows(
            session,
            EmployeeRow.employee_id,
            EmployeeRow.name,
            EmployeeRow.role,
            EmployeeRow.hire_date,
        ),
        "equipment": _rows(
            session,
            EquipmentAssetRow.employee_id,
            EquipmentAssetRow.item,
            EquipmentAssetRow.acquired_on,
        ),
        "catalog": _rows(session, CatalogItemRow.item, CatalogItemRow.unit_cost_usd),
        "aliases": _rows(session, CatalogAliasRow.alias, CatalogAliasRow.item),
        "policies": _rows(
            session,
            RolePolicyRow.role,
            RolePolicyRow.item,
            RolePolicyRow.entitled,
            RolePolicyRow.refresh_months,
            RolePolicyRow.min_tenure_months,
        ),
    }


def test_seed_is_idempotent(db_session: Session) -> None:
    seed(db_session, TODAY)
    first = _snapshot(db_session)
    seed(db_session, TODAY)
    assert _snapshot(db_session) == first
    assert len(first["employees"]) == 10


def test_seed_writes_demo_employees_and_not_e9999(db_session: Session) -> None:
    seed(db_session, TODAY)
    ids = set(db_session.scalars(select(EmployeeRow.employee_id)))
    assert ids == {f"E{n}" for n in range(1001, 1011)}


def test_reseeding_on_a_later_day_moves_dates_without_duplicates(db_session: Session) -> None:
    seed(db_session, TODAY)
    later = date(2027, 2, 1)
    seed(db_session, later)

    monitors = db_session.scalars(
        select(EquipmentAssetRow.acquired_on).where(
            EquipmentAssetRow.employee_id == "E1001", EquipmentAssetRow.item == "monitor"
        )
    ).all()
    assert monitors == [add_months(later, -56)]


def test_seed_keeps_existing_review_tickets(db_session: Session) -> None:
    seed(db_session, TODAY)
    db_session.add(
        ReviewTicketRow(
            employee_id="E1003",
            request_text="coffee spill",
            request_key="k",
            escalation_code="EXCEPTION_CLAIMED",
            reason="EXCEPTION_CLAIMED: coffee spill",
            status="PENDING_REVIEW",
            created_at=datetime(2026, 11, 26, tzinfo=UTC),
        )
    )
    db_session.flush()
    seed(db_session, TODAY)
    assert db_session.scalars(select(ReviewTicketRow)).one().request_key == "k"
