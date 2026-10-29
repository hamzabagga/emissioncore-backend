from decimal import Decimal

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
def setup_a(client, admin_a):
    """OrgA : une company + 2 versions du facteur électricité (kWh)."""
    company = client.post(
        "/companies", json={"name": "Usine", "industry": "textile"}, headers=admin_a
    ).json()
    v1 = client.post("/emission-factors", headers=admin_a, json={
        "source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.40",
        "valid_from": "2024-01-01", "valid_to": "2025-01-01",
    }).json()
    v2 = client.post("/emission-factors", headers=admin_a, json={
        "source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.35",
        "valid_from": "2025-01-01", "valid_to": None,
    }).json()
    return {"company": company, "v1": v1, "v2": v2}


def record_payload(company_id, amount="1000", date="2024-06-15", source="electricity", unit="kWh"):
    return {"company_id": company_id, "source": source, "unit": unit,
            "activity_amount": amount, "date": date}


def create_record(client, headers, *args, **kwargs):
    response = client.post("/records", json=record_payload(*args, **kwargs), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------- Calcul ----------

def test_kg_co2e_is_amount_times_factor(client, analyst_a, setup_a):
    record = create_record(client, analyst_a, setup_a["company"]["id"], "1000", "2024-06-15")
    assert Decimal(record["kg_co2e"]) == Decimal("400")  # 1000 * 0.40
    assert record["factor_id"] == setup_a["v1"]["id"]


def test_factor_valid_at_record_date_is_used_not_latest(client, analyst_a, setup_a):
    cid = setup_a["company"]["id"]
    last_day_v1 = create_record(client, analyst_a, cid, "1000", "2024-12-31")
    first_day_v2 = create_record(client, analyst_a, cid, "1000", "2025-01-01")

    assert last_day_v1["factor_id"] == setup_a["v1"]["id"]
    assert Decimal(last_day_v1["kg_co2e"]) == Decimal("400")
    assert first_day_v2["factor_id"] == setup_a["v2"]["id"]   # valid_to exclusif
    assert Decimal(first_day_v2["kg_co2e"]) == Decimal("350")


def test_decimal_precision_no_float_error(client, analyst_a, setup_a):
    record = create_record(client, analyst_a, setup_a["company"]["id"], "0.1", "2025-03-01")
    assert record["kg_co2e"] == "0.035000"  # 0.1 * 0.35 exact


def test_no_valid_factor_for_date_returns_422(client, analyst_a, setup_a):
    response = client.post(
        "/records", json=record_payload(setup_a["company"]["id"], date="2023-06-01"),
        headers=analyst_a,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_valid_factor"


def test_no_factor_for_unit_returns_422(client, analyst_a, setup_a):
    response = client.post(
        "/records", json=record_payload(setup_a["company"]["id"], unit="MWh"), headers=analyst_a
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_valid_factor"


def test_client_cannot_set_kg_co2e(client, analyst_a, setup_a):
    payload = record_payload(setup_a["company"]["id"], "1000", "2024-06-15")
    payload["kg_co2e"] = "1"  # ignoré
    record = client.post("/records", json=payload, headers=analyst_a).json()
    assert Decimal(record["kg_co2e"]) == Decimal("400")


def test_update_date_recalculates_with_new_factor(client, analyst_a, setup_a):
    record = create_record(client, analyst_a, setup_a["company"]["id"], "1000", "2024-06-15")
    updated = client.patch(
        f"/records/{record['id']}", json={"date": "2025-06-15"}, headers=analyst_a
    ).json()
    assert updated["factor_id"] == setup_a["v2"]["id"]
    assert Decimal(updated["kg_co2e"]) == Decimal("350")


def test_negative_amount_returns_422(client, analyst_a, setup_a):
    response = client.post(
        "/records", json=record_payload(setup_a["company"]["id"], amount="-5"), headers=analyst_a
    )
    assert response.status_code == 422


def test_factor_used_by_records_cannot_be_deleted(client, admin_a, analyst_a, setup_a):
    create_record(client, analyst_a, setup_a["company"]["id"], "1000", "2024-06-15")
    response = client.delete(f"/emission-factors/{setup_a['v1']['id']}", headers=admin_a)
    assert response.status_code == 409


# ---------- Filtres et pagination ----------

def test_filter_by_date_range_and_sort(client, analyst_a, setup_a):
    cid = setup_a["company"]["id"]
    for day in ["2024-03-01", "2024-06-01", "2025-02-01"]:
        create_record(client, analyst_a, cid, "10", day)
    response = client.get(
        "/records",
        params={"date_from": "2024-01-01", "date_to": "2024-12-31", "sort": "date", "order": "asc"},
        headers=analyst_a,
    )
    assert [r["date"] for r in response.json()["items"]] == ["2024-03-01", "2024-06-01"]


# ---------- Isolation ----------

def test_cannot_create_record_on_other_org_company(client, admin_b, setup_a):
    response = client.post(
        "/records", json=record_payload(setup_a["company"]["id"]), headers=admin_b
    )
    assert response.status_code == 404


def test_records_of_other_org_are_invisible(client, analyst_a, admin_b, setup_a):
    record = create_record(client, analyst_a, setup_a["company"]["id"])
    assert client.get(f"/records/{record['id']}", headers=admin_b).status_code == 404
    assert client.patch(
        f"/records/{record['id']}", json={"activity_amount": "1"}, headers=admin_b
    ).status_code == 404
    assert client.delete(f"/records/{record['id']}", headers=admin_b).status_code == 404
    assert client.get("/records", headers=admin_b).json()["items"] == []


def test_org_b_cannot_use_org_a_factors(client, admin_b):
    company_b = client.post(
        "/companies", json={"name": "B", "industry": "x"}, headers=admin_b
    ).json()
    # OrgB n'a aucun facteur : les facteurs d'OrgA ne doivent pas être utilisés
    response = client.post("/records", json=record_payload(company_b["id"]), headers=admin_b)
    assert response.status_code == 422


# ---------- Rôles ----------

def test_viewer_can_read_but_not_write_records(client, analyst_a, auth_headers, setup_a):
    record = create_record(client, analyst_a, setup_a["company"]["id"])
    viewer = auth_headers("viewer@a.io", role="viewer")
    assert client.get(f"/records/{record['id']}", headers=viewer).status_code == 200
    assert client.post(
        "/records", json=record_payload(setup_a["company"]["id"]), headers=viewer
    ).status_code == 403
    assert client.delete(f"/records/{record['id']}", headers=viewer).status_code == 403


def test_analyst_can_create_and_delete_records(client, analyst_a, setup_a):
    record = create_record(client, analyst_a, setup_a["company"]["id"])
    assert client.delete(f"/records/{record['id']}", headers=analyst_a).status_code == 204


# ---------- Valeurs limites et null ----------

@pytest.fixture
def huge_factor(client, admin_a, setup_a):
    response = client.post("/emission-factors", headers=admin_a, json={
        "source": "fuel", "unit": "litre", "kg_co2e_per_unit": "999999999999",
        "valid_from": "2024-01-01", "valid_to": None,
    })
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("amount", ["1000", "999999999999.999999"])
def test_create_record_with_too_large_kg_co2e_returns_422(client, admin_a, setup_a, huge_factor,
                                                          amount):
    # avant la correction : 500 (dépassement Numeric(20, 6) ou InvalidOperation de quantize)
    response = client.post(
        "/records",
        json=record_payload(setup_a["company"]["id"], amount=amount, source="fuel", unit="litre"),
        headers=admin_a,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "kg_co2e_too_large"


def test_update_record_to_too_large_kg_co2e_returns_422_and_keeps_record(client, admin_a, setup_a,
                                                                         huge_factor):
    record = create_record(client, admin_a, setup_a["company"]["id"], amount="1", source="fuel",
                           unit="litre")
    response = client.patch(
        f"/records/{record['id']}", json={"activity_amount": "1000"}, headers=admin_a
    )
    assert response.status_code == 422, response.text
    unchanged = client.get(f"/records/{record['id']}", headers=admin_a).json()
    assert unchanged["activity_amount"] == record["activity_amount"]


@pytest.mark.parametrize("field", ["source", "unit", "activity_amount", "date"])
def test_update_record_with_null_value_returns_422(client, admin_a, setup_a, field):
    # avant la correction : null ignoré en silence -> 200 sans rien changer
    record = create_record(client, admin_a, setup_a["company"]["id"])
    response = client.patch(f"/records/{record['id']}", json={field: None}, headers=admin_a)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
