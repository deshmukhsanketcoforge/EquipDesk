"""SQLAlchemy models. The schema is created by Alembic (migrations/), and a test
checks the migrations and these models agree."""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from server.codes import EscalationCode


def normalized_check(column: str) -> str:
    """SQL that holds when `column` is lower-case, trimmed and single-spaced.

    Mirrors server.repositories.base.normalize_name, so exact key lookups work.
    """
    return rf"{column} = lower(regexp_replace(btrim({column}), '\s+', ' ', 'g'))"


ESCALATION_CODES_CHECK = "escalation_code IN ({})".format(
    ", ".join(f"'{code.value}'" for code in EscalationCode)
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class EmployeeRow(Base):
    __tablename__ = "employees"

    employee_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(32))
    hire_date: Mapped[date] = mapped_column(Date)


class CatalogItemRow(Base):
    __tablename__ = "catalog_items"
    __table_args__ = (
        CheckConstraint("unit_cost_usd >= 0", name="unit_cost_non_negative"),
        CheckConstraint(normalized_check("item"), name="item_normalized"),
    )

    item: Mapped[str] = mapped_column(String(32), primary_key=True)
    unit_cost_usd: Mapped[int] = mapped_column(Integer)


class CatalogAliasRow(Base):
    __tablename__ = "catalog_aliases"
    __table_args__ = (CheckConstraint(normalized_check("alias"), name="alias_normalized"),)

    alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    item: Mapped[str] = mapped_column(ForeignKey("catalog_items.item"))


class EquipmentAssetRow(Base):
    __tablename__ = "equipment_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.employee_id", ondelete="CASCADE"), index=True
    )
    item: Mapped[str] = mapped_column(ForeignKey("catalog_items.item"))
    acquired_on: Mapped[date] = mapped_column(Date)


class RolePolicyRow(Base):
    __tablename__ = "role_policies"
    __table_args__ = (
        CheckConstraint(
            "(entitled AND refresh_months > 0) OR (NOT entitled AND refresh_months IS NULL)",
            name="refresh_iff_entitled",
        ),
        CheckConstraint("min_tenure_months >= 0", name="min_tenure_non_negative"),
    )

    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    item: Mapped[str] = mapped_column(ForeignKey("catalog_items.item"), primary_key=True)
    entitled: Mapped[bool]
    refresh_months: Mapped[int | None] = mapped_column(Integer)
    min_tenure_months: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class ReviewTicketRow(Base):
    __tablename__ = "review_tickets"
    __table_args__ = (
        # Idempotency: at most one open ticket per employee and request.
        Index(
            "uq_review_tickets_open_request",
            "employee_id",
            "request_key",
            unique=True,
            postgresql_where=text("status = 'PENDING_REVIEW'"),
        ),
        # Closed set (PLAN.md D4). Changing EscalationCode needs a migration.
        CheckConstraint(ESCALATION_CODES_CHECK, name="escalation_code_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # No foreign key: unknown employees (e.g. E9999) are escalated too.
    employee_id: Mapped[str] = mapped_column(String(16))
    request_text: Mapped[str] = mapped_column(Text)
    request_key: Mapped[str] = mapped_column(String(64))
    escalation_code: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
