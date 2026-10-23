import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

GroupBy = Literal["month", "quarter", "source"]


class SummaryBucket(BaseModel):
    key: str  # "2024-06" (month), "2024-Q2" (quarter) ou "electricity" (source)
    kg_co2e: Decimal
    record_count: int


class SummaryOut(BaseModel):
    company_id: int
    date_from: dt.date | None
    date_to: dt.date | None
    group_by: GroupBy
    total_kg_co2e: Decimal
    record_count: int
    breakdown: list[SummaryBucket]