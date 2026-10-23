from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.api.deps import require_roles
from app.core.config import settings
from app.core.errors import AppError
from app.models import User


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_login_success_returns_token_pair(client, make_user):
    make_user("alice@orga.io")
    response = client.post(
        "/auth/login", json={"email": "alice@orga.io", "password": "password123"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_login_wrong_password_returns_401_with_error_format(client, make_user):
    make_user("alice@orga.io")
    response = client.post("/auth/login", json={"email": "alice@orga.io", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_login_unknown_email_gives_same_error_as_wrong_password(client, make_user):
    make_user("alice@orga.io")
    wrong_pw = client.post("/auth/login", json={"email": "alice@orga.io", "password": "x"})
    unknown = client.post("/auth/login", json={"email": "nobody@orga.io", "password": "x"})
    # On ne révèle pas quels comptes existent
    assert unknown.status_code == wrong_pw.status_code == 401
    assert unknown.json() == wrong_pw.json()


def test_login_invalid_email_format_returns_422(client):
    response = client.post("/auth/login", json={"email": "not-an-email", "password": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_me_returns_current_user(client, make_user, login):
    make_user("alice@orga.io", role="analyst")
    tokens = login("alice@orga.io")
    response = client.get("/auth/me", headers=bearer(tokens["access_token"]))
    assert response.status_code == 200
    assert response.json()["email"] == "alice@orga.io"
    assert response.json()["role"] == "analyst"
    assert "password_hash" not in response.json()


def test_me_without_token_returns_401(client):
    assert client.get("/auth/me").status_code == 401


def test_me_with_garbage_token_returns_401(client):
    assert client.get("/auth/me", headers=bearer("not.a.jwt")).status_code == 401


def test_me_with_expired_token_returns_401(client, make_user):
    user = make_user("alice@orga.io")
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "type": "access",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    assert client.get("/auth/me", headers=bearer(expired)).status_code == 401


def test_me_with_token_signed_by_another_key_returns_401(client, make_user):
    user = make_user("alice@orga.io")
    forged = jwt.encode(
        {
            "sub": str(user.id),
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "attacker-secret-attacker-secret-0123456789",
        algorithm="HS256",
    )
    assert client.get("/auth/me", headers=bearer(forged)).status_code == 401


def test_refresh_returns_new_working_access_token(client, make_user, login):
    make_user("alice@orga.io")
    tokens = login("alice@orga.io")
    response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    new_access = response.json()["access_token"]
    assert client.get("/auth/me", headers=bearer(new_access)).status_code == 200


def test_refresh_token_cannot_be_used_as_access_token(client, make_user, login):
    make_user("alice@orga.io")
    tokens = login("alice@orga.io")
    assert client.get("/auth/me", headers=bearer(tokens["refresh_token"])).status_code == 401


def test_access_token_cannot_be_used_to_refresh(client, make_user, login):
    make_user("alice@orga.io")
    tokens = login("alice@orga.io")
    response = client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert response.status_code == 401


def test_deleted_user_token_is_rejected(client, make_user, login, db_session_factory):
    user = make_user("alice@orga.io")
    tokens = login("alice@orga.io")
    with db_session_factory() as db:
        db.delete(db.get(User, user.id))
        db.commit()
    # Token encore valide cryptographiquement, mais l'utilisateur n'existe plus
    me = client.get("/auth/me", headers=bearer(tokens["access_token"]))
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "unauthorized"
    refresh = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 401


@pytest.mark.parametrize(
    ("allowed", "role", "is_allowed"),
    [
        (("admin",), "admin", True),
        (("admin",), "analyst", False),
        (("admin",), "viewer", False),
        (("analyst", "admin"), "analyst", True),
        (("analyst", "admin"), "viewer", False),
        (("viewer", "analyst", "admin"), "viewer", True),
    ],
)
def test_require_roles(allowed, role, is_allowed):
    checker = require_roles(*allowed)
    user = User(email="x@orga.io", role=role)
    if is_allowed:
        assert checker(user) is user
    else:
        with pytest.raises(AppError) as exc_info:
            checker(user)
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "forbidden"
