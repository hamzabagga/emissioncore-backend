import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.factor import Source


class RecordCreate(BaseModel):
    company_id: int
    source: Source
    unit: str = Field(min_length=1, max_length=20)
    activity_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    date: dt.date
    # kg_co2e n'est PAS ici : il est toujours calculé par le serveur


class RecordUpdate(BaseModel):
    source: Source | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    activity_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    date: dt.date | None = None

    @field_validator("source", "unit", "activity_amount", "date")
    @classmethod
    def reject_explicit_null(cls, value):
        # Champ absent = inchangé ; null explicite = 422 (au lieu d'être ignoré en silence)
        if value is None:
            raise ValueError("cannot be null")
        return value


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: int
    company_id: int
    source: str
    unit: str
    activity_amount: Decimal
    date: dt.date
    kg_co2e: Decimal
    factor_id: int
    created_at: dt.datetime
