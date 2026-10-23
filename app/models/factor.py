from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

SOURCES = ("electricity", "fuel", "travel", "other")


class EmissionFactor(TimestampMixin, Base):
    __tablename__ = "emission_factors"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_factors_id_org"),
        CheckConstraint(
            "source IN ('electricity', 'fuel', 'travel', 'other')", name="ck_factors_source"
        ),
        CheckConstraint("kg_co2e_per_unit >= 0", name="ck_factors_value_non_negative"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_factors_valid_range"
        ),

    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String(20))
    unit: Mapped[str] = mapped_column(String(20))
    kg_co2e_per_unit: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)  # exclusif