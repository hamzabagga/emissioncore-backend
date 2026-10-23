from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.errors import unauthorized
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.models import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_pair(user_id: int) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
    )


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, db: DbSession):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # Même message que l'email existe ou non : on ne révèle pas quels comptes existent
    if user is None or not verify_password(body.password, user.password_hash):
        raise unauthorized("Invalid email or password")
    return _token_pair(user.id)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: DbSession):
    try:
        user_id = decode_token(body.refresh_token, expected_type="refresh")
    except ValueError:
        raise unauthorized("Invalid or expired refresh token") from None
    if db.get(User, user_id) is None:
        raise unauthorized("User no longer exists")
    return _token_pair(user_id)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user