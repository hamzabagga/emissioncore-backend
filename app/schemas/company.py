from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    industry: str = Field(min_length=1, max_length=100)


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    industry: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("name", "industry")
    @classmethod
    def reject_explicit_null(cls, value):
        # Champ absent = inchangé ; null explicite = 422 (colonnes NOT NULL)
        if value is None:
            raise ValueError("cannot be null")
        return value


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: int
    name: str
    industry: str
    created_at: datetime