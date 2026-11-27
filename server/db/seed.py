"""Write the seed data to Postgres. Idempotent: running it again gives the same rows.

Run with `make seed` (applies migrations first). Review tickets are never touched.
"""

from datetime import date

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from server.clock import SystemClock
from server.config import Settings
from server.db.models import (
    CatalogAliasRow,
    CatalogItemRow,
    EmployeeRow,
    EquipmentAssetRow,
    RolePolicyRow,
)
from server.db.seed_data import build_seed_data
from server.repositories.base import normalize_name


def seed(session: Session, today: date) -> None:
    """Upsert catalog, policies and demo employees; replace their equipment and aliases.

    Does not commit; the caller owns the transaction.
    """
    data = build_seed_data(today)
    items = [normalize_name(c.item) for c in data.catalog]
    roles = sorted({p.role for p in data.policies})
    employee_ids = [e.employee_id for e in data.employees]

    for catalog_item in data.catalog:
        session.merge(
            CatalogItemRow(
                item=normalize_name(catalog_item.item), unit_cost_usd=catalog_item.unit_cost_usd
            )
        )
    session.flush()

    session.execute(delete(CatalogAliasRow).where(CatalogAliasRow.item.in_(items)))
    session.add_all(
        CatalogAliasRow(alias=normalize_name(alias), item=normalize_name(c.item))
        for c in data.catalog
        for alias in c.aliases
    )

    session.execute(delete(RolePolicyRow).where(RolePolicyRow.role.in_(roles)))
    session.add_all(
        RolePolicyRow(
            role=p.role,
            item=p.item,
            entitled=p.entitled,
            refresh_months=p.refresh_months,
            min_tenure_months=p.min_tenure_months,
        )
        for p in data.policies
    )

    for e in data.employees:
        session.merge(
            EmployeeRow(employee_id=e.employee_id, name=e.name, role=e.role, hire_date=e.hire_date)
        )
    session.flush()

    session.execute(
        delete(EquipmentAssetRow).where(EquipmentAssetRow.employee_id.in_(employee_ids))
    )
    session.add_all(
        EquipmentAssetRow(employee_id=e.employee_id, item=a.item, acquired_on=a.acquired_on)
        for e in data.employees
        for a in e.equipment
    )
    session.flush()


def main() -> None:  # pragma: no cover - thin CLI, exercised by `make seed`
    engine = create_engine(Settings().database_url)
    today = SystemClock().today()
    with Session(engine) as session, session.begin():
        seed(session, today)
    print(f"Seeded catalog, policies and demo employees (dates relative to {today}).")


if __name__ == "__main__":  # pragma: no cover
    main()
