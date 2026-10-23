import datetime as dt
from typing import Literal

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from app.api.deps import DbSession, ReadUser, WriteUser
from app.api.pagination import paginate
from app.api.scoping import get_company_or_404, get_record_or_404
from app.models import EmissionRecord
from app.schemas.common import Page
from app.schemas.factor import Source
from app.schemas.record import RecordCreate, RecordOut, RecordUpdate
from app.services.records import calculate_emissions

router = APIRouter(prefix="/records", tags=["emission records"])

SORT_COLUMNS = {
    "id": EmissionRecord.id,
    "date": EmissionRecord.date,
    "kg_co2e": EmissionRecord.kg_co2e,
}


@router.get("", response_model=Page[RecordOut])
def list_records(
    user: ReadUser,
    db: DbSession,
    company_id: int | None = None,
    source: Source | None = None,
    date_from: dt.date | None = None,  # inclus
    date_to: dt.date | None = None,    # inclus
    sort: Literal["id", "date", "kg_co2e"] = "date",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
):
    stmt = select(EmissionRecord).where(EmissionRecord.organization_id == user.organization_id)
    if company_id is not None:
        stmt = stmt.where(EmissionRecord.company_id == company_id)
    if source:
        stmt = stmt.where(EmissionRecord.source == source)
    if date_from:
        stmt = stmt.where(EmissionRecord.date >= date_from)
    if date_to:
        stmt = stmt.where(EmissionRecord.date <= date_to)
    items, next_cursor = paginate(
        db, stmt, EmissionRecord.id, SORT_COLUMNS[sort], order == "desc", limit, cursor
    )
    return Page(items=items, next_cursor=next_cursor)


@router.post("", response_model=RecordOut, status_code=201)
def create_record(body: RecordCreate, user: WriteUser, db: DbSession):
    # 404 si la company n'appartient pas à l'organisation de l'utilisateur
    get_company_or_404(db, body.company_id, user.organization_id)
    factor, kg_co2e = calculate_emissions(
        db, user.organization_id, body.source, body.unit, body.activity_amount, body.date
    )
    record = EmissionRecord(
        organization_id=user.organization_id,
        **body.model_dump(),
        kg_co2e=kg_co2e,
        factor_id=factor.id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/{record_id}", response_model=RecordOut)
def get_record(record_id: int, user: ReadUser, db: DbSession):
    return get_record_or_404(db, record_id, user.organization_id)


@router.patch("/{record_id}", response_model=RecordOut)
def update_record(record_id: int, body: RecordUpdate, user: WriteUser, db: DbSession):
    record = get_record_or_404(db, record_id, user.organization_id)
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(record, field, value)
    # Toute modification peut changer le facteur applicable -> on recalcule toujours
    factor, kg_co2e = calculate_emissions(
        db, user.organization_id, record.source, record.unit, record.activity_amount, record.date
    )
    record.factor_id = factor.id
    record.kg_co2e = kg_co2e
    db.commit()
    db.refresh(record)
    return record


@router.delete("/{record_id}", status_code=204)
def delete_record(record_id: int, user: WriteUser, db: DbSession):
    record = get_record_or_404(db, record_id, user.organization_id)
    db.delete(record)
    db.commit()
    return Response(status_code=204)
