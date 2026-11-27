"""EQP-3: repository contract.

Every test runs against both implementations: the Postgres repository and
the in-memory fake. Service tests (EQP-4 onward) use the fake, so it must
behave exactly like the real one.
"""

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from server.codes import EscalationCode
from server.dates import add_months
from server.db.seed import seed
from server.db.seed_data import build_seed_data
from server.records import EquipmentItem, NewTicket
from server.repositories.base import Repository
from server.repositories.memory import InMemoryRepository
from server.repositories.sql import SqlRepository

TODAY = date(2026, 11, 26)
CATALOG_ITEMS = {
    "laptop",
    "monitor",
    "keyboard",
    "mouse",
    "headset",
    "dock",
    "phone",
    "workstation",
}


@pytest.fixture(params=["memory", "postgres"])
def repo(request: pytest.FixtureRequest) -> Repository:
    if request.param == "memory":
        return InMemoryRepository.from_seed(build_seed_data(TODAY))
    session: Session = request.getfixturevalue("db_session")
    seed(session, TODAY)
    return SqlRepository(session)


# --- employees -------------------------------------------------------------


def test_get_employee_returns_role_hire_date_and_equipment(repo: Repository) -> None:
    employee = repo.get_employee("E1001")
    assert employee is not None
    assert employee.employee_id == "E1001"
    assert employee.role == "engineer"
    assert employee.name
    assert employee.hire_date < TODAY
    assert EquipmentItem("monitor", add_months(TODAY, -56)) in employee.equipment


def test_equipment_is_ordered_oldest_first(repo: Repository) -> None:
    employee = repo.get_employee("E1001")
    assert employee is not None
    dates = [item.acquired_on for item in employee.equipment]
    assert len(dates) >= 2
    assert dates == sorted(dates)


def test_employee_without_equipment_has_an_empty_tuple(repo: Repository) -> None:
    employee = repo.get_employee("E1004")
    assert employee is not None
    assert employee.equipment == ()


@pytest.mark.parametrize("employee_id", ["E9999", "e1001", "", "E1001 "])
def test_unknown_employee_id_returns_none(repo: Repository, employee_id: str) -> None:
    # IDs are matched exactly; input normalization belongs to the service layer.
    assert repo.get_employee(employee_id) is None


# --- policies --------------------------------------------------------------


def test_role_policies_cover_every_catalog_item(repo: Repository) -> None:
    policies = {p.item: p for p in repo.get_role_policies("engineer")}
    assert set(policies) == CATALOG_ITEMS
    assert policies["workstation"].entitled is True
    assert policies["workstation"].refresh_months == 48
    assert policies["phone"].entitled is False
    assert policies["phone"].refresh_months is None
    assert policies["laptop"].min_tenure_months == 3


@pytest.mark.parametrize("role", ["intern", "ENGINEER", ""])
def test_role_without_policy_returns_nothing(repo: Repository, role: str) -> None:
    assert repo.get_role_policies(role) == []


# --- catalog ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "item"),
    [
        ("laptop", "laptop"),
        ("LAPTOP", "laptop"),
        ("  Screen ", "monitor"),
        ("display", "monitor"),
        ("docking   station", "dock"),
        ("Desktop Workstation", "workstation"),
        ("cell phone", "phone"),
    ],
)
def test_catalog_lookup_by_name_or_alias(repo: Repository, name: str, item: str) -> None:
    found = repo.find_catalog_item(name)
    assert found is not None
    assert found.item == item


def test_catalog_item_carries_cost_and_aliases(repo: Repository) -> None:
    found = repo.find_catalog_item("workstation")
    assert found is not None
    assert found.unit_cost_usd == 3200
    assert found.aliases == ("desktop workstation",)


@pytest.mark.parametrize("name", ["desktop", "chair", "", "   ", "monitors"])
def test_unknown_catalog_name_returns_none(repo: Repository, name: str) -> None:
    assert repo.find_catalog_item(name) is None


# --- review tickets --------------------------------------------------------


def _new_ticket(request_key: str = "k1", reason: str = "first wording") -> NewTicket:
    return NewTicket(
        employee_id="E1003",
        request_text="I spilled coffee on my laptop",
        request_key=request_key,
        escalation_code=EscalationCode.EXCEPTION_CLAIMED,
        reason=f"EXCEPTION_CLAIMED: {reason}",
        created_at=datetime(2026, 11, 26, 9, 30, tzinfo=UTC),
    )


def test_first_ticket_is_created_pending_review(repo: Repository) -> None:
    ticket, created = repo.get_or_create_open_ticket(_new_ticket())
    assert created is True
    assert re.fullmatch(r"TKT-\d{6}", ticket.ticket_id)
    assert ticket.status == "PENDING_REVIEW"
    assert ticket.employee_id == "E1003"
    assert ticket.escalation_code is EscalationCode.EXCEPTION_CLAIMED
    assert ticket.reason == "EXCEPTION_CLAIMED: first wording"


def test_same_request_key_returns_the_existing_open_ticket(repo: Repository) -> None:
    first, _ = repo.get_or_create_open_ticket(_new_ticket(reason="first wording"))
    second, created = repo.get_or_create_open_ticket(_new_ticket(reason="other wording"))
    assert created is False
    assert second == first


def test_different_request_key_creates_a_new_ticket(repo: Repository) -> None:
    first, _ = repo.get_or_create_open_ticket(_new_ticket("k1"))
    second, created = repo.get_or_create_open_ticket(_new_ticket("k2"))
    assert created is True
    assert second.ticket_id != first.ticket_id


def test_ticket_created_at_is_utc_and_identical_on_replay(repo: Repository) -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    new = replace(_new_ticket(), created_at=datetime(2026, 11, 26, 15, 0, tzinfo=ist))
    first, _ = repo.get_or_create_open_ticket(new)
    second, _ = repo.get_or_create_open_ticket(new)
    assert first.created_at == datetime(2026, 11, 26, 9, 30, tzinfo=UTC)
    assert first.created_at.utcoffset() == timedelta(0)
    assert second.created_at == first.created_at
    assert second == first


def test_naive_created_at_is_rejected(repo: Repository) -> None:
    naive = replace(_new_ticket(), created_at=datetime(2026, 11, 26, 9, 30))
    with pytest.raises(ValueError, match="time zone"):
        repo.get_or_create_open_ticket(naive)
