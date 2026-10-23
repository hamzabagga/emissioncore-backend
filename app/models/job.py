from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

JOB_TYPES = ("import", "recalculation")
JOB_STATUSES = ("pending", "processing", "completed", "failed")


class Job(TimestampMixin, Base):
    """File de tâches de fond stockée dans PostgreSQL (pas de Redis).

    Le worker réserve un job avec SELECT ... FOR UPDATE SKIP LOCKED.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("type IN ('import', 'recalculation')", name="ck_jobs_type"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')", name="ck_jobs_status"
        ),
        # La company appartient forcément à la même organisation que le job
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="CASCADE",
            name="fk_jobs_company_same_org",
        ),
        # Idempotence de l'import : un même fichier pour une même company = un seul job.
        # Résiste aussi à deux uploads simultanés (la base refuse le 2e).
        UniqueConstraint("company_id", "file_sha256", name="uq_jobs_company_file"),
        # Le worker cherche les jobs en attente, du plus ancien au plus récent
        Index("ix_jobs_status_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int | None] = mapped_column(nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")

    # Import : contenu du fichier + empreinte pour l'idempotence
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # Progression et résultat
    total_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    processed_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    success_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    error_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    # Rapport d'erreurs par ligne : [{"row": 3, "error": "..."}]
    errors: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
