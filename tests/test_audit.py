import pytest


@pytest.fixture
def admin_a(auth_headers):
    return auth_headers("admin@a.io", role="admin", org_name="OrgA")


@pytest.fixture
def analyst_a(auth_headers):
    return auth_headers("analyst@a.io", role="analyst", org_name="OrgA")


@pytest.fixture
def admin_b(auth_headers):
    return auth_headers("admin@b.io", role="admin", org_name="OrgB")


@pytest.fixture
def factor(client, admin_a):
    return client.post("/emission-factors", headers=admin_a, json={
        "source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.40",
        "valid_from": "2024-01-01", "valid_to": None,
    }).json()


@pytest.fixture
def company(client, admin_a):
    return client.post(
        "/companies", json={"name": "Usine", "industry": "x"}, headers=admin_a
    ).json()


def audit(client, headers, **params):
    response = client.get("/audit", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_factor_create_update_delete_are_logged(client, admin_a, factor):
    me = client.get("/auth/me", headers=admin_a).json()
    client.patch(
        f"/emission-factors/{factor['id']}", json={"kg_co2e_per_unit": "0.42"}, headers=admin_a
    )
    client.delete(f"/emission-factors/{factor['id']}", headers=admin_a)

    logs = audit(client, admin_a, entity="emission_factor", entity_id=factor["id"])
    assert [log["action"] for log in logs] == ["delete", "update", "create"]  # récent d'abord
    assert all(log["user_id"] == me["id"] for log in logs)  # QUI
    # QUOI : seul le champ modifié, avec ancienne et nouvelle valeur
    assert logs[1]["changes"] == {
        "kg_co2e_per_unit": {"old": "0.4", "new": "0.42"}
    }
    assert logs[2]["changes"]["kg_co2e_per_unit"] == "0.4"


def test_record_changes_are_logged_with_user(client, admin_a, analyst_a, factor, company):
    analyst = client.get("/auth/me", headers=analyst_a).json()
    record = client.post("/records", headers=analyst_a, json={
        "company_id": company["id"], "source": "electricity", "unit": "kWh",
        "activity_amount": "1000", "date": "2024-06-01",
    }).json()
    client.patch(
        f"/records/{record['id']}", json={"activity_amount": "500"}, headers=analyst_a
    )

    logs = audit(client, admin_a, entity="emission_record", entity_id=record["id"])
    assert [log["action"] for log in logs] == ["update", "create"]
    assert logs[0]["user_id"] == analyst["id"]
    # le recalcul automatique du kg_co2e apparaît aussi dans le diff
    assert logs[0]["changes"]["activity_amount"] == {"old": "1000", "new": "500"}
    assert logs[0]["changes"]["kg_co2e"] == {"old": "400", "new": "200"}


def test_failed_change_is_not_logged(client, admin_a, factor):
    # Chevauchement refusé (409) : aucune trace d'un changement qui n'a pas eu lieu
    response = client.post("/emission-factors", headers=admin_a, json={
        "source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.30",
        "valid_from": "2024-06-01", "valid_to": None,
    })
    assert response.status_code == 409
    assert len(audit(client, admin_a, entity="emission_factor")) == 1  # seulement la création


def test_no_entry_when_nothing_changes(client, admin_a, factor):
    client.patch(
        f"/emission-factors/{factor['id']}", json={"kg_co2e_per_unit": "0.40"}, headers=admin_a
    )
    assert [log["action"] for log in audit(client, admin_a)] == ["create"]


def test_audit_is_isolated_per_organization(client, admin_a, admin_b, factor):
    assert audit(client, admin_b) == []
    assert audit(client, admin_b, entity="emission_factor", entity_id=factor["id"]) == []


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_only_admin_can_read_audit(client, auth_headers, role):
    headers = auth_headers(f"{role}@a.io", role=role)
    assert client.get("/audit", headers=headers).status_code == 403
