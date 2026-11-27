"""EQP-3: schema and migrations on real Postgres."""

from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.codes import EscalationCode
from server.db.migrate import head_revision
from server.db.models import Base, CatalogAliasRow, CatalogItemRow, ReviewTicketRow


def test_migrations_create_every_table(db_engine: Engine) -> None:
    tables = set(inspect(db_engine).get_table_names())
    assert {
        "employees",
        "equipment_assets",
        "catalog_items",
        "catalog_aliases",
        "role_policies",
        "review_tickets",
    } <= tables


def test_database_is_at_the_head_revision(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        current = conn.execute(text("select version_num from alembic_version")).scalar_one()
    assert current == head_revision()


def test_migrations_match_the_models(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def _ticket(request_key: str, status: str = "PENDING_REVIEW") -> ReviewTicketRow:
    return ReviewTicketRow(
        employee_id="E1003",
        request_text="coffee spill on laptop",
        request_key=request_key,
        escalation_code="EXCEPTION_CLAIMED",
        reason="EXCEPTION_CLAIMED: coffee spill",
        status=status,
        created_at=datetime(2026, 11, 26, tzinfo=UTC),
    )


def test_only_one_open_ticket_per_employee_and_request(db_session: Session) -> None:
    db_session.add(_ticket("k1"))
    db_session.flush()
    db_session.add(_ticket("k1"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_closed_ticket_does_not_block_a_new_open_one(db_session: Session) -> None:
    db_session.add(_ticket("k1", status="CLOSED"))
    db_session.add(_ticket("k1"))
    db_session.flush()


def test_tickets_for_unknown_employees_are_allowed(db_session: Session) -> None:
    # Escalating an unknown employee (E9999) must still create a ticket.
    row = _ticket("k2")
    row.employee_id = "E9999"
    db_session.add(row)
    db_session.flush()


@pytest.mark.parametrize("code", [c.value for c in EscalationCode])
def test_every_escalation_code_can_be_stored(db_session: Session, code: str) -> None:
    row = _ticket(f"key-{code}")
    row.escalation_code = code
    db_session.add(row)
    db_session.flush()


def test_unknown_escalation_code_is_rejected(db_session: Session) -> None:
    row = _ticket("k3")
    row.escalation_code = "NOT_A_CODE"
    db_session.add(row)
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("item", ["Laptop", " laptop", "laptop ", "big  screen"])
def test_catalog_item_names_must_be_normalized(db_session: Session, item: str) -> None:
    db_session.add(CatalogItemRow(item=item, unit_cost_usd=10))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("alias", ["Docking Station", " dock", "docking  station"])
def test_catalog_aliases_must_be_normalized(db_session: Session, alias: str) -> None:
    db_session.add(CatalogItemRow(item="dock", unit_cost_usd=280))
    db_session.flush()
    db_session.add(CatalogAliasRow(alias=alias, item="dock"))
    with pytest.raises(IntegrityError):
        db_session.flush()
