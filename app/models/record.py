from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EmissionRecord(TimestampMixin, Base):
    __tablename__ = "emission_records"
    __table_args__ = (
        # Isolation garantie par la base : la company et le facteur
        # doivent appartenir à la MÊME organisation que le record.
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="CASCADE",
            name="fk_records_company_same_org",
        ),
        ForeignKeyConstraint(
            ["factor_id", "organization_id"],
            ["emission_factors.id", "emission_factors.organization_id"],
            ondelete="RESTRICT",  # on ne supprime pas un facteur utilisé
            name="fk_records_factor_same_org",
        ),
        CheckConstraint(
            "source IN ('electricity', 'fuel', 'travel', 'other')", name="ck_records_source"
        ),
        CheckConstraint("activity_amount >= 0", name="ck_records_amount_non_negative"),
        # Filtres par company + plage de dates, summary, pagination
        Index("ix_records_org_company_date", "organization_id", "company_id", "date"),
        # Recalcul : retrouver vite les records d'un facteur
        Index("ix_records_factor", "factor_id"),
        # Idempotence de l'import : une ligne d'un fichier = au plus un record,
        # même si le job est relancé après un crash
        UniqueConstraint("import_job_id", "import_row", name="uq_records_import_row"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column()
    company_id: Mapped[int] = mapped_column()
    source: Mapped[str] = mapped_column(String(20))
    unit: Mapped[str] = mapped_column(String(20))
    activity_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    date: Mapped[date_type] = mapped_column(Date)
    kg_co2e: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    factor_id: Mapped[int] = mapped_column()
    # Renseignés uniquement pour les records créés par un import CSV
    import_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    import_row: Mapped[int | None] = mapped_column(nullable=True)
