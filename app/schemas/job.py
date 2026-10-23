from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RowError(BaseModel):
    row: int
    error: str


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    status: str
    company_id: int | None
    total_rows: int
    processed_rows: int
    success_rows: int
    error_rows: int
    errors: list[RowError]
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
