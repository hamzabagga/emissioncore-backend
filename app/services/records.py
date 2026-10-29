from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import EmissionFactor
from app.services.factors import find_valid_factor

KG_PRECISION = Decimal("0.000001")  # 6 décimales, comme la colonne Numeric(20, 6)


def calculate_emissions(
    db: Session, organization_id: int, source: str, unit: str, amount: Decimal, on_date: date
) -> tuple[EmissionFactor, Decimal]:
    """kg_co2e = amount * facteur valide à la date du record."""
    factor = find_valid_factor(db, organization_id, source, unit, on_date)
    if factor is None:
        raise AppError(
            422,
            "no_valid_factor",
            f"No emission factor valid on {on_date} for source '{source}' and unit '{unit}'",
            details={"source": source, "unit": unit, "date": str(on_date)},
        )
    kg_co2e = (amount * factor.kg_co2e_per_unit).quantize(KG_PRECISION, rounding=ROUND_HALF_UP)
    return factor, kg_co2e
