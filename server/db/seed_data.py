"""Synthetic seed data: catalog, policies and demo employees.

Catalog and policies mirror docs/requirements.md (a test checks they match).
Employee dates are relative to `today` (PLAN.md D6), so every demo path
works whatever day the demo runs.
"""

from dataclasses import dataclass
from datetime import date

from server.dates import add_months
from server.records import CatalogItem, Employee, EquipmentItem, RolePolicy

CATALOG: tuple[CatalogItem, ...] = (
    CatalogItem("laptop", 1800, ("notebook",)),
    CatalogItem("monitor", 350, ("display", "screen")),
    CatalogItem("keyboard", 90),
    CatalogItem("mouse", 60),
    CatalogItem("headset", 150, ("headphones",)),
    CatalogItem("dock", 280, ("docking station",)),
    CatalogItem("phone", 900, ("cell phone", "mobile", "smartphone")),
    CatalogItem("workstation", 3200, ("desktop workstation",)),
)

# Refresh period in years per role and item. Missing item = not entitled.
REFRESH_YEARS: dict[str, dict[str, int]] = {
    "standard": {
        "laptop": 4,
        "monitor": 3,
        "keyboard": 2,
        "mouse": 2,
        "headset": 2,
        "dock": 4,
    },
    "engineer": {
        "laptop": 3,
        "monitor": 3,
        "keyboard": 2,
        "mouse": 2,
        "headset": 2,
        "dock": 3,
        "workstation": 4,
    },
    "manager": {
        "laptop": 2,
        "monitor": 3,
        "keyboard": 2,
        "mouse": 2,
        "headset": 2,
        "dock": 2,
        "phone": 3,
    },
    "contractor": {"keyboard": 2, "mouse": 2, "headset": 2},
}

MIN_TENURE_MONTHS = 3
"""For a laptop (every entitled role) and for anything a contractor requests."""


@dataclass(frozen=True)
class SeedData:
    employees: tuple[Employee, ...]
    catalog: tuple[CatalogItem, ...]
    policies: tuple[RolePolicy, ...]


def _policies() -> tuple[RolePolicy, ...]:
    policies = []
    for role, refresh in REFRESH_YEARS.items():
        for catalog_item in CATALOG:
            item = catalog_item.item
            years = refresh.get(item)
            entitled = years is not None
            needs_tenure = entitled and (item == "laptop" or role == "contractor")
            policies.append(
                RolePolicy(
                    role=role,
                    item=item,
                    entitled=entitled,
                    refresh_months=years * 12 if years is not None else None,
                    min_tenure_months=MIN_TENURE_MONTHS if needs_tenure else 0,
                )
            )
    return tuple(policies)


def _employees(today: date) -> tuple[Employee, ...]:
    def ago(months: int) -> date:
        return add_months(today, -months)

    def employee(
        employee_id: str, name: str, role: str, hired_months_ago: int, *equipment: tuple[str, int]
    ) -> Employee:
        items = sorted(
            (EquipmentItem(item, ago(months)) for item, months in equipment),
            key=lambda a: (a.acquired_on, a.item),
        )
        return Employee(employee_id, name, role, ago(hired_months_ago), tuple(items))

    return (
        # A: approve. Last monitor ~4.7 years old; engineer monitor refresh is 3 years.
        employee("E1001", "Asha Rao", "engineer", 72, ("monitor", 56), ("laptop", 18)),
        # B: deny. Last monitor 9 months old (COOLDOWN_NOT_ELAPSED).
        employee("E1002", "Ben Okafor", "engineer", 36, ("laptop", 24), ("monitor", 9)),
        # C: escalate. Manager laptop ~1.7 years old (2-year refresh) plus a coffee-spill claim.
        employee("E1003", "Carla Mendes", "manager", 48, ("laptop", 20), ("phone", 12)),
        # Hired last month: a laptop request fails the 3-month tenure.
        employee("E1004", "Dev Patel", "standard", 1),
        # E: escalate. Laptop dated before the hire date (CONFLICTING_RECORDS).
        employee("E1005", "Elena Petrova", "engineer", 24, ("laptop", 36)),
        # Contractor: a laptop request fails role entitlement.
        employee("E1006", "Farid Haddad", "contractor", 12, ("keyboard", 10)),
        # D: escalate. Vague request ("something better for my desk").
        employee("E1007", "Grace Kim", "standard", 24, ("laptop", 24), ("monitor", 24)),
        # Engineer without a workstation: a workstation request hits HIGH_COST.
        employee("E1008", "Hiro Tanaka", "engineer", 60, ("monitor", 30), ("laptop", 12)),
        # G: escalate. Laptop a year old; request text tries to override policy.
        employee("E1009", "Isabel Cruz", "standard", 36, ("laptop", 12)),
        # Role with no policy (UNKNOWN_ROLE).
        employee("E1010", "Jonas Weber", "intern", 2),
    )


def build_seed_data(today: date) -> SeedData:
    return SeedData(employees=_employees(today), catalog=CATALOG, policies=_policies())
