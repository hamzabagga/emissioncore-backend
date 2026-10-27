from typing import Literal

from fastapi import APIRouter, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, DbSession, ReadUser
from app.api.pagination import paginate
from app.api.scoping import get_company_or_404
from app.core.errors import conflict
from app.models import Company
from app.schemas.common import Page
from app.schemas.company import CompanyCreate, CompanyOut, CompanyUpdate
from app.services.audit import log_company_records_delete

router = APIRouter(prefix="/companies", tags=["companies"])

UNIQUE_VIOLATION = "23505"

SORT_COLUMNS = {"id": Company.id, "name": Company.name}


def _commit_or_name_conflict(db: DbSession) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Seul le doublon de nom devient un 409 ; toute autre erreur DB ne doit pas être masquée
        if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION:
            raise conflict("A company with this name already exists") from None
        raise


@router.get("", response_model=Page[CompanyOut])
def list_companies(
    user: ReadUser,
    db: DbSession,
    industry: str | None = None,
    sort: Literal["id", "name"] = "id",
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
):
    stmt = select(Company).where(Company.organization_id == user.organization_id)
    if industry:
        stmt = stmt.where(Company.industry == industry)
    items, next_cursor = paginate(
        db, stmt, Company.id, SORT_COLUMNS[sort], order == "desc", limit, cursor
    )
    return Page(items=items, next_cursor=next_cursor)


@router.post("", response_model=CompanyOut, status_code=201)
def create_company(body: CompanyCreate, user: AdminUser, db: DbSession):
    company = Company(organization_id=user.organization_id, **body.model_dump())
    db.add(company)
    _commit_or_name_conflict(db)
    db.refresh(company)
    return company


@router.get("/{company_id}", response_model=CompanyOut)
def get_company(company_id: int, user: ReadUser, db: DbSession):
    return get_company_or_404(db, company_id, user.organization_id)


@router.patch("/{company_id}", response_model=CompanyOut)
def update_company(company_id: int, body: CompanyUpdate, user: AdminUser, db: DbSession):
    company = get_company_or_404(db, company_id, user.organization_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(company, field, value)
    _commit_or_name_conflict(db)
    db.refresh(company)
    return company


@router.delete("/{company_id}", status_code=204)
def delete_company(company_id: int, user: AdminUser, db: DbSession):
    # verrou : aucun record ne peut être ajouté entre l'audit et la suppression
    company = get_company_or_404(db, company_id, user.organization_id, lock="update")
    log_company_records_delete(db, user, company.id)
    db.delete(company)
    db.commit()
    return Response(status_code=204)