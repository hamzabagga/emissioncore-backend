from datetime import date
from decimal import Decimal

from sqlalchemy import text
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


# Une entrée "delete" par record de la company, en une seule requête (même format que
# snapshot() : trim_scale donne "0.4" comme Decimal.normalize, une date donne "YYYY-MM-DD").
_LOG_COMPANY_RECORDS_DELETE_SQL = text("""
    INSERT INTO audit_logs (organization_id, user_id, entity, entity_id, action, changes)
    SELECT r.organization_id, :user_id, 'emission_record', r.id, 'delete',
           jsonb_build_object(
               'company_id', r.company_id,
               'source', r.source,
               'unit', r.unit,
               'activity_amount', trim_scale(r.activity_amount)::text,
               'date', r.date::text,
               'kg_co2e', trim_scale(r.kg_co2e)::text,
               'factor_id', r.factor_id
           )
    FROM emission_records AS r
    WHERE r.organization_id = :org_id AND r.company_id = :company_id
""")


def log_company_records_delete(db: Session, user: User, company_id: int) -> None:
    """Supprimer une company supprime ses records (ON DELETE CASCADE) : on les trace
    dans la même transaction, sinon des records disparaîtraient sans audit."""
    db.execute(
        _LOG_COMPANY_RECORDS_DELETE_SQL,
        {"user_id": user.id, "org_id": user.organization_id, "company_id": company_id},
    )
