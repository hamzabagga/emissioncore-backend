"""add audit_logs table

Revision ID: c3d4e5f6a7b8
Revises: b7e1c2d3f4a5
Create Date: 2026-10-23 22:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b7e1c2d3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("entity", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "entity IN ('emission_record', 'emission_factor')", name="ck_audit_entity"
        ),
        sa.CheckConstraint("action IN ('create', 'update', 'delete')", name="ck_audit_action"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_audit_org", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_audit_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_org_entity", "audit_logs", ["organization_id", "entity", "entity_id", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_org_entity", table_name="audit_logs")
    op.drop_table("audit_logs")
