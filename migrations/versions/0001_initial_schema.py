"""Initial schema: employees, equipment, catalog, policies, review tickets.

Revision ID: 0001
Revises:
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("employee_id", sa.String(16), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("hire_date", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("employee_id", name="pk_employees"),
    )
    op.create_table(
        "catalog_items",
        sa.Column("item", sa.String(32), nullable=False),
        sa.Column("unit_cost_usd", sa.Integer(), nullable=False),
        sa.CheckConstraint("unit_cost_usd >= 0", name="ck_catalog_items_unit_cost_non_negative"),
        sa.CheckConstraint(
            r"item = lower(regexp_replace(btrim(item), '\s+', ' ', 'g'))",
            name="ck_catalog_items_item_normalized",
        ),
        sa.PrimaryKeyConstraint("item", name="pk_catalog_items"),
    )
    op.create_table(
        "catalog_aliases",
        sa.Column("alias", sa.String(64), nullable=False),
        sa.Column("item", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["item"], ["catalog_items.item"], name="fk_catalog_aliases_item_catalog_items"
        ),
        sa.CheckConstraint(
            r"alias = lower(regexp_replace(btrim(alias), '\s+', ' ', 'g'))",
            name="ck_catalog_aliases_alias_normalized",
        ),
        sa.PrimaryKeyConstraint("alias", name="pk_catalog_aliases"),
    )
    op.create_table(
        "equipment_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.String(16), nullable=False),
        sa.Column("item", sa.String(32), nullable=False),
        sa.Column("acquired_on", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.employee_id"],
            name="fk_equipment_assets_employee_id_employees",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item"], ["catalog_items.item"], name="fk_equipment_assets_item_catalog_items"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_equipment_assets"),
    )
    op.create_index(
        "ix_equipment_assets_employee_id", "equipment_assets", ["employee_id"], unique=False
    )
    op.create_table(
        "role_policies",
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("item", sa.String(32), nullable=False),
        sa.Column("entitled", sa.Boolean(), nullable=False),
        sa.Column("refresh_months", sa.Integer(), nullable=True),
        sa.Column("min_tenure_months", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "(entitled AND refresh_months > 0) OR (NOT entitled AND refresh_months IS NULL)",
            name="ck_role_policies_refresh_iff_entitled",
        ),
        sa.CheckConstraint(
            "min_tenure_months >= 0", name="ck_role_policies_min_tenure_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["item"], ["catalog_items.item"], name="fk_role_policies_item_catalog_items"
        ),
        sa.PrimaryKeyConstraint("role", "item", name="pk_role_policies"),
    )
    op.create_table(
        "review_tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.String(16), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("escalation_code", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "escalation_code IN ('EXCEPTION_CLAIMED', 'AMBIGUOUS_ITEM', 'MULTIPLE_ITEMS', "
            "'REQUEST_CONTRADICTS_DATA', 'POLICY_OVERRIDE_ATTEMPT', 'TOOL_NEEDS_REVIEW', "
            "'GUARDRAIL_OVERRIDE', 'OTHER')",
            name="ck_review_tickets_escalation_code_known",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_tickets"),
    )
    op.create_index(
        "uq_review_tickets_open_request",
        "review_tickets",
        ["employee_id", "request_key"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING_REVIEW'"),
    )


def downgrade() -> None:
    op.drop_index("uq_review_tickets_open_request", table_name="review_tickets")
    op.drop_table("review_tickets")
    op.drop_table("role_policies")
    op.drop_index("ix_equipment_assets_employee_id", table_name="equipment_assets")
    op.drop_table("equipment_assets")
    op.drop_table("catalog_aliases")
    op.drop_table("catalog_items")
    op.drop_table("employees")
