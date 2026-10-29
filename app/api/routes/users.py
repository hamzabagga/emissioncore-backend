from fastapi import APIRouter, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, DbSession
from app.api.pagination import paginate
from app.core.errors import AppError, conflict, not_found
from app.core.security import hash_password
from app.models import User
from app.schemas.common import Page
from app.schemas.user import Role, UserAdminOut, UserCreate, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


def _get_user_or_404(db: DbSession, user_id: int, organization_id: int) -> User:
    user = db.scalar(
        select(User).where(User.id == user_id, User.organization_id == organization_id)
    )
    if user is None:
        raise not_found("User")
    return user


@router.get("", response_model=Page[UserAdminOut])
def list_users(
    admin: AdminUser,
    db: DbSession,
    role: Role | None = None,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
):
    stmt = select(User).where(User.organization_id == admin.organization_id)
    if role:
        stmt = stmt.where(User.role == role)
    items, next_cursor = paginate(db, stmt, User.id, User.id, False, limit, cursor)
    return Page(items=items, next_cursor=next_cursor)


@router.post("", response_model=UserAdminOut, status_code=201)
def create_user(body: UserCreate, admin: AdminUser, db: DbSession):
    user = User(
        email=body.email.lower(),
        role=body.role,
        organization_id=admin.organization_id,  # toujours l'organisation de l'admin
        password_hash=hash_password(body.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict("A user with this email already exists") from None
    db.refresh(user)
    return user


@router.get("/{user_id}", response_model=UserAdminOut)
def get_user(user_id: int, admin: AdminUser, db: DbSession):
    return _get_user_or_404(db, user_id, admin.organization_id)


@router.patch("/{user_id}", response_model=UserAdminOut)
def update_user(user_id: int, body: UserUpdate, admin: AdminUser, db: DbSession):
    user = _get_user_or_404(db, user_id, admin.organization_id)
    if user.id == admin.id and body.role is not None and body.role != "admin":
        # sinon l'organisation peut se retrouver sans aucun admin
        raise AppError(409, "conflict", "You cannot remove your own admin role")
    if body.role is not None:
        user.role = body.role
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, admin: AdminUser, db: DbSession):
    user = _get_user_or_404(db, user_id, admin.organization_id)
    if user.id == admin.id:
        raise AppError(409, "conflict", "You cannot delete your own account")
    db.delete(user)
    db.commit()
    return Response(status_code=204)