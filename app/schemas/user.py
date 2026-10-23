from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["admin", "analyst", "viewer"]


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Role


class UserUpdate(BaseModel):
    role: Role | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str
    organization_id: int
    created_at: datetime
    # password_hash n'est volontairement PAS ici