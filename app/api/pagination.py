import base64
import json
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import Session

from app.core.errors import AppError


def _encode_cursor(sort_value, row_id: int) -> str:
    if isinstance(sort_value, (date, Decimal)):
        sort_value = str(sort_value)
    raw = json.dumps({"v": sort_value, "id": row_id}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str, sort_column):
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        value, row_id = data["v"], int(data["id"])
        python_type = sort_column.type.python_type
        if python_type is date:
            value = date.fromisoformat(value)
        elif python_type is Decimal:
            value = Decimal(value)
        return value, row_id
    except Exception:
        raise AppError(422, "invalid_cursor", "Cursor is invalid") from None


def paginate(
    db: Session,
    stmt: Select,
    id_column,
    sort_column,
    descending: bool,
    limit: int,
    cursor: str | None,
):
    """Pagination par curseur (keyset) sur (sort_column, id)."""
    if cursor:
        value, last_id = _decode_cursor(cursor, sort_column)
        if descending:
            stmt = stmt.where(
                or_(sort_column < value, and_(sort_column == value, id_column < last_id))
            )
        else:
            stmt = stmt.where(
                or_(sort_column > value, and_(sort_column == value, id_column > last_id))
            )

    if descending:
        stmt = stmt.order_by(sort_column.desc(), id_column.desc())
    else:
        stmt = stmt.order_by(sort_column.asc(), id_column.asc())

    rows = list(db.scalars(stmt.limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more:
        last = rows[-1]
        next_cursor = _encode_cursor(getattr(last, sort_column.key), last.id)
    return rows, next_cursor