"""EQP-3: the seed data (pure, no database).

The seeded catalog and policies must equal the tables in docs/requirements.md,
and each demo employee must set up the path PLAN.md §5 describes.
"""

import re
from datetime import date
from pathlib import Path

import pytest

from server.dates import add_months
from server.db.seed_data import SeedData, build_seed_data
from server.records import Employee

TODAY = date(2026, 11, 26)
DOC = Path(__file__).resolve().parents[1] / "docs" / "requirements.md"

NO_ALIASES = "\u2013"  # the en dash the doc uses for "no aliases"

POLICY_COLUMNS = [
    ["laptop"],
    ["monitor"],
    ["keyboard", "mouse", "headset"],
    ["dock"],
    ["phone"],
    ["workstation"],
]


@pytest.fixture(scope="module")
def data() -> SeedData:
    return build_seed_data(TODAY)


def _table_rows(section_heading: str) -> list[list[str]]:
    doc = DOC.read_text(encoding="utf-8")
    start = doc.index(f"\n## {section_heading}")
    end = doc.find("\n## ", start + 1)
    rows = []
    for line in doc[start:end].splitlines():
        if line.startswith("| ") and not line.startswith("| ---") and "---" not in line:
            rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows[1:]  # drop the header row


def test_policies_match_the_requirements_doc(data: SeedData) -> None:
    expected: dict[tuple[str, str], int | None] = {}
    for role, *cells in _table_rows("Policy"):
        for items, cell in zip(POLICY_COLUMNS, cells, strict=True):
            match = re.fullmatch(r"1 per (\d+) yrs", cell)
            for item in items:
                expected[(role, item)] = int(match.group(1)) * 12 if match else None

    seeded = {(p.role, p.item): (p.refresh_months if p.entitled else None) for p in data.policies}
    assert seeded == expected


def test_minimum_tenure_matches_the_requirements_doc(data: SeedData) -> None:
    # Doc: 3 months for a laptop (every entitled role) and for anything a contractor requests.
    for p in data.policies:
        needs_tenure = p.entitled and (p.item == "laptop" or p.role == "contractor")
        assert p.min_tenure_months == (3 if needs_tenure else 0), (p.role, p.item)


def test_catalog_matches_the_requirements_doc(data: SeedData) -> None:
    expected = {}
    for item, aliases, cost in _table_rows("Catalog"):
        names = [] if aliases == NO_ALIASES else aliases.split(",")
        alias_set = frozenset(name.strip() for name in names)
        expected[item] = (int(cost.replace(",", "")), alias_set)

    seeded = {c.item: (c.unit_cost_usd, frozenset(c.aliases)) for c in data.catalog}
    assert seeded == expected


def test_demo_employees_are_present_and_e9999_is_not(data: SeedData) -> None:
    ids = {e.employee_id for e in data.employees}
    assert ids == {f"E{n}" for n in range(1001, 1011)}
    assert "E9999" not in ids


def _employee(data: SeedData, employee_id: str) -> Employee:
    return next(e for e in data.employees if e.employee_id == employee_id)


def _latest(data: SeedData, employee_id: str, item: str) -> date:
    return max(a.acquired_on for a in _employee(data, employee_id).equipment if a.item == item)


def test_case_a_e1001_last_monitor_is_older_than_three_years(data: SeedData) -> None:
    assert _employee(data, "E1001").role == "engineer"
    assert _latest(data, "E1001", "monitor") == add_months(TODAY, -56)  # ~4.7 years


def test_case_b_e1002_last_monitor_is_nine_months_old(data: SeedData) -> None:
    assert _employee(data, "E1002").role == "engineer"
    assert _latest(data, "E1002", "monitor") == add_months(TODAY, -9)


def test_case_c_e1003_manager_laptop_is_inside_its_two_year_refresh(data: SeedData) -> None:
    assert _employee(data, "E1003").role == "manager"
    assert _latest(data, "E1003", "laptop") == add_months(TODAY, -20)  # ~1.7 years


def test_e1004_was_hired_last_month(data: SeedData) -> None:
    assert _employee(data, "E1004").hire_date == add_months(TODAY, -1)


def test_case_e_e1005_has_equipment_dated_before_hire(data: SeedData) -> None:
    e1005 = _employee(data, "E1005")
    assert any(a.acquired_on < e1005.hire_date for a in e1005.equipment)


def test_e1006_is_a_contractor(data: SeedData) -> None:
    assert _employee(data, "E1006").role == "contractor"


def test_e1008_is_an_engineer_without_a_workstation(data: SeedData) -> None:
    e1008 = _employee(data, "E1008")
    assert e1008.role == "engineer"
    assert all(a.item != "workstation" for a in e1008.equipment)


def test_case_g_e1009_standard_laptop_is_a_year_old(data: SeedData) -> None:
    assert _employee(data, "E1009").role == "standard"
    assert _latest(data, "E1009", "laptop") == add_months(TODAY, -12)


def test_e1010_has_a_role_without_policy(data: SeedData) -> None:
    roles_with_policy = {p.role for p in data.policies}
    assert _employee(data, "E1010").role not in roles_with_policy


def test_all_equipment_uses_catalog_items(data: SeedData) -> None:
    catalog = {c.item for c in data.catalog}
    for employee in data.employees:
        assert {a.item for a in employee.equipment} <= catalog


def test_dates_move_with_today() -> None:
    later = date(2027, 6, 30)
    assert _latest(build_seed_data(later), "E1001", "monitor") == add_months(later, -56)
