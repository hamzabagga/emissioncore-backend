from datetime import date

from sqlalchemy import Date, cast, func, literal, select
from sqlalchemy.orm import Session

from app.core.errors import conflict
from app.models import EmissionFactor


def _period(start, end):
    # [start, end) ; end NULL = sans fin
    return func.daterange(cast(start, Date), cast(end, Date), literal("[)"))


def ensure_no_overlap(
    db: Session,
    organization_id: int,
    source: str,
    unit: str,
    valid_from: date,
    valid_to: date | None,
    exclude_id: int | None = None,
) -> None:
    stmt = select(EmissionFactor).where(
        EmissionFactor.organization_id == organization_id,
        EmissionFactor.source == source,
        EmissionFactor.unit == unit,
        _period(EmissionFactor.valid_from, EmissionFactor.valid_to).op("&&")(
            _period(literal(valid_from, Date), literal(valid_to, Date))
        ),
    )
    if exclude_id is not None:
        stmt = stmt.where(EmissionFactor.id != exclude_id)
    existing = db.scalar(stmt.limit(1))
    if existing:
        raise conflict(
            "Factor period overlaps an existing factor for the same source and unit",
            details={
                "conflicting_factor_id": existing.id,
                "valid_from": str(existing.valid_from),
                "valid_to": str(existing.valid_to) if existing.valid_to else None,
            },
        )


def find_valid_factor(
    db: Session, organization_id: int, source: str, unit: str, on_date: date
) -> EmissionFactor | None:
    """Le facteur valide à une date donnée : valid_from <= date < valid_to (ou sans fin)."""
    return db.scalar(
        select(EmissionFactor).where(
            EmissionFactor.organization_id == organization_id,
            EmissionFactor.source == source,
            EmissionFactor.unit == unit,
            EmissionFactor.valid_from <= on_date,
            (EmissionFactor.valid_to.is_(None)) | (EmissionFactor.valid_to > on_date),
        )
    )