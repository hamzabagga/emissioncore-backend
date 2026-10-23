"""add jobs table and import columns on records

Revision ID: b7e1c2d3f4a5
Revises: 934fb5d4bf75
Create Date: 2026-10-23 21:30:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e1c2d3f4a5"
down_revision: Union[str, Sequence[str], None] = "934fb5d4bf75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("file_content", sa.LargeBinary(), nullable=True),
        sa.Column("total_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "errors", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("type IN ('import', 'recalculation')", name="ck_jobs_type"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')", name="ck_jobs_status"
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            name="fk_jobs_company_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_jobs_created_by", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_jobs_org", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "file_sha256", name="uq_jobs_company_file"),
    )
    op.create_index("ix_jobs_organization_id", "jobs", ["organization_id"])
    op.create_index("ix_jobs_status_id", "jobs", ["status", "id"])

    op.add_column("emission_records", sa.Column("import_job_id", sa.Integer(), nullable=True))
    op.add_column("emission_records", sa.Column("import_row", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_records_import_job", "emission_records", "jobs",
        ["import_job_id"], ["id"], ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_records_import_row", "emission_records", ["import_job_id", "import_row"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_records_import_row", "emission_records", type_="unique")
    op.drop_constraint("fk_records_import_job", "emission_records", type_="foreignkey")
    op.drop_column("emission_records", "import_row")
    op.drop_column("emission_records", "import_job_id")
    op.drop_index("ix_jobs_status_id", table_name="jobs")
    op.drop_index("ix_jobs_organization_id", table_name="jobs")
    op.drop_table("jobs")
