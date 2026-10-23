import datetime as dt

from fastapi import APIRouter, Query

from app.api.deps import DbSession, ReadUser
from app.api.scoping import get_company_or_404
from app.core.errors import AppError
from app.schemas.summary import GroupBy, SummaryOut
from app.services.summary import company_summary

router = APIRouter(prefix="/companies", tags=["summary"])


@router.get("/{company_id}/summary", response_model=SummaryOut)
def get_summary(
    company_id: int,
    user: ReadUser,
    db: DbSession,
    # "from" est un mot réservé en Python -> alias
    date_from: dt.date | None = Query(None, alias="from", description="inclus"),
    date_to: dt.date | None = Query(None, alias="to", description="inclus"),
    group_by: GroupBy = "month",
):
    if date_from and date_to and date_from > date_to:
        raise AppError(422, "validation_error", "'from' must be before or equal to 'to'")
    # 404 si la company appartient à une autre organisation
    get_company_or_404(db, company_id, user.organization_id)
    return company_summary(db, user.organization_id, company_id, date_from, date_to, group_by)