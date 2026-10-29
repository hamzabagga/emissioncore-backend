from typing import Literal

from fastapi import APIRouter, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, DbSession, ReadUser
from app.api.pagination import paginate
from app.api.scoping import get_factor_or_404
from app.core.errors import AppError, conflict
from app.models import EmissionFactor
from app.schemas.common import Page
from app.schemas.factor import FactorCreate, FactorOut, FactorUpdate, Source
from app.services.audit import FACTOR_FIELDS, log_change, snapshot
from app.services.factors import ensure_no_overlap
from app.services.recalculation import enqueue_recalculation

router = APIRouter(prefix="/emission-factors", tags=["emission factors"])

EXCLUSION_VIOLATION = "23P01"
FOREIGN_KEY_VIOLATION = "23503"

SORT_COLUMNS = {"id": EmissionFactor.id, "valid_from": EmissionFactor.valid_from}


def _commit_or_conflict(db: DbSession, message: str, sqlstate: str = EXCLUSION_VIOLATION) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Seule la violation attendue (ex. exclusion 23P01 lors d'une requête
        # concurrente) devient un 409 ; toute autre erreur DB ne doit pas être masquée.
        if getattr(exc.orig, "sqlstate", None) == sqlstate:
            raise conflict(message) from None
        raise


@router.get("", response_model=Page[FactorOut])
def list_factors(
    user: ReadUser,
    db: DbSession,
    source: Source | None = None,
    unit: str | None = None,
    sort: Literal["id", "valid_from"] = "valid_from",
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
):
    stmt = select(EmissionFactor).where(EmissionFactor.organization_id == user.organization_id)
    if source:
        stmt = stmt.where(EmissionFactor.source == source)
    if unit:
        stmt = stmt.where(EmissionFactor.unit == unit)
    items, next_cursor = paginate(
        db, stmt, EmissionFactor.id, SORT_COLUMNS[sort], order == "desc", limit, cursor
    )
    return Page(items=items, next_cursor=next_cursor)


@router.post("", response_model=FactorOut, status_code=201)
def create_factor(body: FactorCreate, user: AdminUser, db: DbSession):
    ensure_no_overlap(
        db, user.organization_id, body.source, body.unit, body.valid_from, body.valid_to
    )
    factor = EmissionFactor(organization_id=user.organization_id, **body.model_dump())
    db.add(factor)
    try:
        db.flush()  # obtient factor.id ; la contrainte d'exclusion peut déjà refuser ici
    except IntegrityError as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION:
            raise conflict("Factor period overlaps an existing factor") from None
        raise
    log_change(db, user, "emission_factor", factor.id, "create",
               after=snapshot(factor, FACTOR_FIELDS))
    # Le nouveau facteur peut s'appliquer à des records existants : recalcul en tâche de fond,
    # mis en file dans la même transaction (pas de facteur modifié sans recalcul prévu)
    enqueue_recalculation(db, user.organization_id, user.id)
    _commit_or_conflict(db, "Factor period overlaps an existing factor")
    db.refresh(factor)
    return factor


@router.get("/{factor_id}", response_model=FactorOut)
def get_factor(factor_id: int, user: ReadUser, db: DbSession):
    return get_factor_or_404(db, factor_id, user.organization_id)


@router.patch("/{factor_id}", response_model=FactorOut)
def update_factor(factor_id: int, body: FactorUpdate, user: AdminUser, db: DbSession):
    factor = get_factor_or_404(db, factor_id, user.organization_id)
    before = snapshot(factor, FACTOR_FIELDS)
    changes = body.model_dump(exclude_unset=True)

    new_from = changes.get("valid_from", factor.valid_from)
    new_to = changes.get("valid_to", factor.valid_to)
    if new_to is not None and new_to <= new_from:
        raise AppError(422, "validation_error", "valid_to must be after valid_from")
    ensure_no_overlap(
        db, user.organization_id, factor.source, factor.unit, new_from, new_to,
        exclude_id=factor.id,
    )

    for field, value in changes.items():
        setattr(factor, field, value)
    after = snapshot(factor, FACTOR_FIELDS)
    log_change(db, user, "emission_factor", factor.id, "update", before=before, after=after)
    if after != before:
        # Les records rattachés à ce facteur doivent refléter sa nouvelle valeur / période
        enqueue_recalculation(db, user.organization_id, user.id)
    _commit_or_conflict(db, "Factor period overlaps an existing factor")
    db.refresh(factor)
    return factor


@router.delete("/{factor_id}", status_code=204)
def delete_factor(factor_id: int, user: AdminUser, db: DbSession):
    factor = get_factor_or_404(db, factor_id, user.organization_id)
    log_change(db, user, "emission_factor", factor.id, "delete",
               before=snapshot(factor, FACTOR_FIELDS))
    db.delete(factor)
    # FK RESTRICT : impossible de supprimer un facteur utilisé par des records
    _commit_or_conflict(
        db, "Factor is used by emission records and cannot be deleted", FOREIGN_KEY_VIOLATION
    )
    return Response(status_code=204)