from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Company(TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        # cible de la clé étrangère composite depuis emission_records
        UniqueConstraint("id", "organization_id", name="uq_companies_id_org"),
        # pas deux companies du même nom dans une organisation (-> 409)
        UniqueConstraint("organization_id", "name", name="uq_companies_org_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str] = mapped_column(String(100))