from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Source = Literal["electricity", "fuel", "travel", "other"]


class FactorCreate(BaseModel):
    source: Source
    unit: str = Field(min_length=1, max_length=20)
    kg_co2e_per_unit: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    valid_from: date
    valid_to: date | None = None  # exclusif ; None = toujours valide

    @model_validator(mode="after")
    def check_period(self):
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


class FactorUpdate(BaseModel):
    kg_co2e_per_unit: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    valid_from: date | None = None
    valid_to: date | None = None  # null explicite autorisé : facteur sans date de fin

    @field_validator("kg_co2e_per_unit", "valid_from")
    @classmethod
    def reject_explicit_null(cls, value):
        # Champ absent = inchangé ; null explicite = 422 (colonnes NOT NULL)
        if value is None:
            raise ValueError("cannot be null")
        return value


class FactorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: int
    source: str
    unit: str
    kg_co2e_per_unit: Decimal
    valid_from: date
    valid_to: date | None
    created_at: datetime