from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.models import Company, EmissionFactor, EmissionRecord


def get_company_or_404(db: Session, company_id: int, organization_id: int) -> Company:
    # Le filtre organization_id rend l'objet INVISIBLE pour les autres organisations
    company = db.scalar(
        select(Company).where(
            Company.id == company_id, Company.organization_id == organization_id
        )
    )
    if company is None:
        raise not_found("Company")
    return company


def get_factor_or_404(db: Session, factor_id: int, organization_id: int) -> EmissionFactor:
    factor = db.scalar(
        select(EmissionFactor).where(
            EmissionFactor.id == factor_id, EmissionFactor.organization_id == organization_id
        )
    )
    if factor is None:
        raise not_found("Emission factor")
    return factor


def get_record_or_404(db: Session, record_id: int, organization_id: int) -> EmissionRecord:
    record = db.scalar(
        select(EmissionRecord).where(
            EmissionRecord.id == record_id, EmissionRecord.organization_id == organization_id
        )
    )
    if record is None:
        raise not_found("Emission record")
    return record
