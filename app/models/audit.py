from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    """Qui a changé quoi et quand, pour les records et les facteurs.

    Écrit dans la MÊME transaction que la modification : pas de changement
    sans trace, et pas de trace pour un changement annulé.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "entity IN ('emission_record', 'emission_factor')", name="ck_audit_entity"
        ),
        CheckConstraint("action IN ('create', 'update', 'delete')", name="ck_audit_action"),
        # GET /audit?entity=&entity_id= : filtre par organisation + entité, plus récent d'abord
        Index("ix_audit_org_entity", "organization_id", "entity", "entity_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    entity: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column()
    action: Mapped[str] = mapped_column(String(10))
    # create : valeurs créées ; delete : valeurs supprimées ;
    # update : {"champ": {"old": ..., "new": ...}} uniquement pour les champs modifiés
    changes: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
