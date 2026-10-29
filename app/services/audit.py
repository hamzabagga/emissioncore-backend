from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import AuditLog, User

RECORD_FIELDS = ("company_id", "source", "unit", "activity_amount", "date", "kg_co2e", "factor_id")
FACTOR_FIELDS = ("source", "unit", "kg_co2e_per_unit", "valid_from", "valid_to")


def _json(value):
    # Decimal et date ne sont pas des types JSON : on les stocke en texte exact.
    # Decimal normalisé : 0.40 (saisi) et 0.400000 (relu en base) donnent tous deux "0.4",
    # sinon on verrait un faux "changement" alors que la valeur est identique.
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    return value


def snapshot(obj, fields: tuple[str, ...]) -> dict:
    return {f: _json(getattr(obj, f)) for f in fields}


def log_change(
    db: Session,
    user: User,
    entity: str,
    entity_id: int,
    action: str,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """Ajoute l'entrée d'audit à la session : elle sera validée avec la modification."""
    if action == "create":
        changes = after
    elif action == "delete":
        changes = before
    else:
        changes = {
            field: {"old": before[field], "new": after[field]}
            for field in after
            if before[field] != after[field]
        }
        if not changes:
            return  # rien n'a vraiment changé : pas d'entrée inutile
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            user_id=user.id,
            entity=entity,
            entity_id=entity_id,
            action=action,
            changes=changes,
        )
    )
