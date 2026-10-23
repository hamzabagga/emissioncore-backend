from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

password_hasher = PasswordHash.recommended()  # Argon2


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def _create_token(user_id: int, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),   # qui
        "type": token_type,    # "access" ou "refresh"
        "iat": now,            # émis à
        "exp": now + expires_delta,  # expire à (vérifié automatiquement par pyjwt)
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> str:
    return _create_token(user_id, "access", timedelta(minutes=settings.access_token_minutes))


def create_refresh_token(user_id: int) -> str:
    return _create_token(user_id, "refresh", timedelta(days=settings.refresh_token_days))


def decode_token(token: str, expected_type: str) -> int:
    """Renvoie l'id utilisateur, ou lève ValueError si le token est invalide."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:  # signature fausse, expiré, mal formé...
        raise ValueError("invalid token") from exc
    if payload.get("type") != expected_type:
        # empêche d'utiliser un refresh token comme access token (et inversement)
        raise ValueError("wrong token type")
    return int(payload["sub"])
               