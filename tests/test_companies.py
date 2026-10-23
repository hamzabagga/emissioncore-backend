import pytest


@pytest.fixture
def org_a_admin(auth_headers):
    return auth_headers("admin@a.io", role="admin", org_name="OrgA")


@pytest.fixture
def org_b_admin(auth_headers):
    return auth_headers("admin@b.io", role="admin", org_name="OrgB")


def create_company(client, headers, name="Usine Textile", industry="textile"):
    response = client.post("/companies", json={"name": name, "industry": industry}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------- CRUD de base ----------

def test_admin_can_create_get_update_delete_company(client, org_a_admin):
    company = create_company(client, org_a_admin)

    got = client.get(f"/companies/{company['id']}", headers=org_a_admin)
    assert got.status_code == 200
    assert got.json()["name"] == "Usine Textile"

    updated = client.patch(
        f"/companies/{company['id']}", json={"industry": "fashion"}, headers=org_a_admin
    )
    assert updated.status_code == 200
    assert updated.json()["industry"] == "fashion"
    assert updated.json()["name"] == "Usine Textile"  # champ non envoyé = inchangé

    assert client.delete(f"/companies/{company['id']}", headers=org_a_admin).status_code == 204
    assert client.get(f"/companies/{company['id']}", headers=org_a_admin).status_code == 404


def test_duplicate_company_name_in_same_org_returns_409(client, org_a_admin):
    create_company(client, org_a_admin, name="Same")
    response = client.post(
        "/companies", json={"name": "Same", "industry": "x"}, headers=org_a_admin
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_same_company_name_allowed_in_different_orgs(client, org_a_admin, org_b_admin):
    create_company(client, org_a_admin, name="Same")
    create_company(client, org_b_admin, name="Same")  # pas de 409


def test_invalid_body_returns_422(client, org_a_admin):
    response = client.post("/companies", json={"name": ""}, headers=org_a_admin)
    assert response.status_code == 422


# ---------- Isolation entre organisations ----------

def test_list_only_returns_own_organization_companies(client, org_a_admin, org_b_admin):
    create_company(client, org_a_admin, name="A1")
    create_company(client, org_b_admin, name="B1")

    names = [c["name"] for c in client.get("/companies", headers=org_a_admin).json()["items"]]
    assert names == ["A1"]


@pytest.mark.parametrize(
    "method, payload",
    [("get", None), ("patch", {"name": "hacked"}), ("delete", None)],
)
def test_guessed_id_from_other_org_returns_404(client, org_a_admin, org_b_admin, method, payload):
    company_a = create_company(client, org_a_admin, name="Secret A")

    kwargs = {"headers": org_b_admin}
    if payload:
        kwargs["json"] = payload
    response = getattr(client, method)(f"/companies/{company_a['id']}", **kwargs)

    # 404 et pas 403 : on ne révèle même pas que la company existe
    assert response.status_code == 404
    # et la company de A est intacte
    still_there = client.get(f"/companies/{company_a['id']}", headers=org_a_admin)
    assert still_there.status_code == 200
    assert still_there.json()["name"] == "Secret A"


# ---------- Rôles ----------

@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_non_admin_cannot_create_company(client, auth_headers, role):
    headers = auth_headers(f"{role}@a.io", role=role)
    response = client.post("/companies", json={"name": "X", "industry": "y"}, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_viewer_can_read_companies(client, org_a_admin, auth_headers):
    create_company(client, org_a_admin)
    viewer = auth_headers("viewer@a.io", role="viewer")
    assert client.get("/companies", headers=viewer).status_code == 200


def test_without_token_returns_401(client):
    assert client.get("/companies").status_code == 401


# ---------- Pagination ----------

def test_cursor_pagination_walks_all_pages_without_duplicates(client, org_a_admin):
    for i in range(5):
        create_company(client, org_a_admin, name=f"C{i}")

    seen, cursor = [], None
    while True:
        params = {"limit": 2, "sort": "name"}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/companies", params=params, headers=org_a_admin).json()
        seen += [c["name"] for c in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == ["C0", "C1", "C2", "C3", "C4"]  # tout, dans l'ordre, sans doublon


def test_invalid_cursor_returns_422(client, org_a_admin):
    response = client.get("/companies", params={"cursor": "garbage"}, headers=org_a_admin)
    assert response.status_code == 422