import pytest


@pytest.fixture
def admin_a(auth_headers):
    return auth_headers("admin@a.io", role="admin", org_name="OrgA")


@pytest.fixture
def admin_b(auth_headers):
    return auth_headers("admin@b.io", role="admin", org_name="OrgB")


def create_user(client, headers, email="new@a.io", role="analyst", password="password123"):
    response = client.post(
        "/users", json={"email": email, "password": password, "role": role}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_creates_user_who_can_then_log_in(client, admin_a):
    user = create_user(client, admin_a, "Bob@A.io", "analyst")
    assert user["email"] == "bob@a.io"
    assert user["role"] == "analyst"
    assert "password_hash" not in user and "password" not in user

    login = client.post("/auth/login", json={"email": "bob@a.io", "password": "password123"})
    assert login.status_code == 200


def test_created_user_belongs_to_admin_organization(client, admin_a):
    me = client.get("/auth/me", headers=admin_a).json()
    user = create_user(client, admin_a)
    assert user["organization_id"] == me["organization_id"]


def test_duplicate_email_returns_409(client, admin_a):
    create_user(client, admin_a, "dup@a.io")
    response = client.post(
        "/users", json={"email": "dup@a.io", "password": "password123", "role": "viewer"},
        headers=admin_a,
    )
    assert response.status_code == 409


@pytest.mark.parametrize("payload", [
    {"email": "x@a.io", "password": "short", "role": "viewer"},     # mot de passe trop court
    {"email": "x@a.io", "password": "password123", "role": "boss"},  # rôle inconnu
    {"email": "not-an-email", "password": "password123", "role": "viewer"},
])
def test_invalid_user_payload_returns_422(client, admin_a, payload):
    assert client.post("/users", json=payload, headers=admin_a).status_code == 422


def test_admin_changes_role_and_it_applies_immediately(client, admin_a):
    create_user(client, admin_a, "bob@a.io", "analyst")
    token = client.post(
        "/auth/login", json={"email": "bob@a.io", "password": "password123"}
    ).json()["access_token"]
    bob = {"Authorization": f"Bearer {token}"}
    bob_id = client.get("/auth/me", headers=bob).json()["id"]

    client.patch(f"/users/{bob_id}", json={"role": "viewer"}, headers=admin_a)
    # même token, mais le rôle est relu en base à chaque requête
    assert client.get("/auth/me", headers=bob).json()["role"] == "viewer"


def test_admin_resets_password(client, admin_a):
    user = create_user(client, admin_a, "bob@a.io")
    client.patch(f"/users/{user['id']}", json={"password": "newpassword1"}, headers=admin_a)
    old = client.post("/auth/login", json={"email": "bob@a.io", "password": "password123"})
    new = client.post("/auth/login", json={"email": "bob@a.io", "password": "newpassword1"})
    assert old.status_code == 401
    assert new.status_code == 200


def test_admin_cannot_delete_or_demote_himself(client, admin_a):
    me = client.get("/auth/me", headers=admin_a).json()
    assert client.delete(f"/users/{me['id']}", headers=admin_a).status_code == 409
    assert client.patch(
        f"/users/{me['id']}", json={"role": "viewer"}, headers=admin_a
    ).status_code == 409


def test_list_users_only_own_organization(client, admin_a, admin_b):
    create_user(client, admin_a, "bob@a.io")
    emails = [u["email"] for u in client.get("/users", headers=admin_b).json()["items"]]
    assert emails == ["admin@b.io"]


@pytest.mark.parametrize("method, payload", [
    ("get", None), ("patch", {"role": "admin"}), ("delete", None),
])
def test_users_of_other_org_return_404(client, admin_a, admin_b, method, payload):
    user_a = create_user(client, admin_a, "bob@a.io", "viewer")
    kwargs = {"headers": admin_b}
    if payload:
        kwargs["json"] = payload
    assert getattr(client, method)(f"/users/{user_a['id']}", **kwargs).status_code == 404
    # le compte de A est intact
    assert client.get(f"/users/{user_a['id']}", headers=admin_a).json()["role"] == "viewer"


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_only_admin_can_manage_users(client, auth_headers, role):
    headers = auth_headers(f"{role}@a.io", role=role)
    assert client.get("/users", headers=headers).status_code == 403
    assert client.post(
        "/users", json={"email": "x@a.io", "password": "password123", "role": "admin"},
        headers=headers,
    ).status_code == 403