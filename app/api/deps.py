from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import forbidden, unauthorized
from app.core.security import decode_token
from app.db.session import get_db
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None:
        raise unauthorized("Missing bearer token")
    try:
        user_id = decode_token(credentials.credentials, expected_type="access")
    except ValueError:
        raise unauthorized("Invalid or expired token") from None
    # On relit l'utilisateur en base : rôle et organisation toujours à jour
    user = db.get(User, user_id)
    if user is None:
        raise unauthorized("User no longer exists")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*allowed_roles: str):
    def checker(user: CurrentUser) -> User:
        if user.role not in allowed_roles:
            raise forbidden()
        return user

    return checker


# Raccourcis utilisés dans les routes
ReadUser = Annotated[User, Depends(require_roles("viewer", "analyst", "admin"))]
WriteUser = Annotated[User, Depends(require_roles("analyst", "admin"))]
AdminUser = Annotated[User, Depends(require_roles("admin"))]