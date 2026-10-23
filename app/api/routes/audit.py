from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import AdminUser, DbSession
from app.api.pagination import paginate
from app.models import AuditLog
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
def list_audit_logs(
    admin: AdminUser,
    db: DbSession,
    entity: Literal["emission_record", "emission_factor"] | None = None,
    entity_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
):
    stmt = select(AuditLog).where(AuditLog.organization_id == admin.organization_id)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    # le plus récent d'abord
    items, next_cursor = paginate(db, stmt, AuditLog.id, AuditLog.id, True, limit, cursor)
    return Page(items=items, next_cursor=next_cursor)
