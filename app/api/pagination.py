import base64
import json
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import Session

from app.core.errors import AppError

MAX_ID = 2**31 - 1  # colonnes id = integer PostgreSQL
MAX_NUMERIC = Decimal("1e20")  # au-delà des colonnes Numeric(18, 6) et Numeric(20, 6)


def _encode_cursor(sort_value, row_id: int) -> str:
    if isinstance(sort_value, (date, Decimal)):
        sort_value = str(sort_value)
    raw = json.dumps({"v": sort_value, "id": row_id}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str, sort_column):
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        value, row_id = data["v"], data["id"]
        if type(row_id) is not int or not 0 < row_id <= MAX_ID:
            raise ValueError("cursor id out of range")
        python_type = sort_column.type.python_type
        if python_type is date:
            value = date.fromisoformat(value)
        elif python_type is Decimal:
            value = Decimal(value)
            # NaN / Infinity / 1e1000000 : pages fausses ou erreur SQL -> curseur invalide
            if not value.is_finite() or abs(value) >= MAX_NUMERIC:
                raise ValueError("cursor value out of range")
        elif type(value) is not python_type:
            # curseur forgé (ex. texte pour une colonne entière) : sinon erreur SQL -> 500
            raise TypeError("cursor value has the wrong type")
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