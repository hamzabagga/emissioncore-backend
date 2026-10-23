import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EmissionRecord
from app.schemas.summary import GroupBy, SummaryBucket, SummaryOut


def _group_key(group_by: GroupBy):
    """Clé de regroupement calculée PAR POSTGRESQL (pas en Python)."""
    if group_by == "month":
        return func.to_char(EmissionRecord.date, "YYYY-MM")
    if group_by == "quarter":
        return func.to_char(EmissionRecord.date, 'YYYY-"Q"Q')
    return EmissionRecord.source


def company_summary(
    db: Session,
    organization_id: int,
    company_id: int,
    date_from: dt.date | None,
    date_to: dt.date | None,
    group_by: GroupBy,
) -> SummaryOut:
    filters = [
        EmissionRecord.organization_id == organization_id,
        EmissionRecord.company_id == company_id,
    ]
    if date_from:
        filters.append(EmissionRecord.date >= date_from)  # inclus
    if date_to:
        filters.append(EmissionRecord.date <= date_to)  # inclus

    key = _group_key(group_by).label("key")
    # Une seule requête SQL : SUM + COUNT + GROUP BY, triée par clé
    rows = db.execute(
        select(
            key,
            func.sum(EmissionRecord.kg_co2e).label("kg_co2e"),
            func.count().label("record_count"),
        )
        .where(*filters)
        .group_by(key)
        .order_by(key)
    ).all()

    breakdown = [
        SummaryBucket(key=row.key, kg_co2e=row.kg_co2e, record_count=row.record_count)
        for row in rows
    ]
    return SummaryOut(
        company_id=company_id,
        date_from=date_from,
        date_to=date_to,
        group_by=group_by,
        total_kg_co2e=sum((b.kg_co2e for b in breakdown), Decimal("0")),
        record_count=sum(b.record_count for b in breakdown),
        breakdown=breakdown,
    )