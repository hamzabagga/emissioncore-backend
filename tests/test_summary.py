from decimal import Decimal

import pytest


@pytest.fixture
def admin_a(auth_headers):
    return auth_headers("admin@a.io", role="admin", org_name="OrgA")


@pytest.fixture
def admin_b(auth_headers):
    return auth_headers("admin@b.io", role="admin", org_name="OrgB")


@pytest.fixture
def company(client, admin_a):
    """Une company avec 2 versions du facteur électricité et 1 facteur fuel.

    Records créés (kg_co2e calculés par l'API) :
      2024-11-15 electricity 1000 kWh x 0.40 = 400
      2024-12-31 electricity 1000 kWh x 0.40 = 400   <- dernier jour de v1
      2025-01-01 electricity 1000 kWh x 0.35 = 350   <- premier jour de v2
      2025-02-10 fuel         100 L    x 2.50 = 250
      2025-04-01 electricity  200 kWh x 0.35 =  70
    """
    c = client.post("/companies", json={"name": "Usine", "industry": "x"}, headers=admin_a).json()
    for f in [
        {"source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.40",
         "valid_from": "2024-01-01", "valid_to": "2025-01-01"},
        {"source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.35",
         "valid_from": "2025-01-01", "valid_to": None},
        {"source": "fuel", "unit": "L", "kg_co2e_per_unit": "2.50",
         "valid_from": "2024-01-01", "valid_to": None},
    ]:
        assert client.post("/emission-factors", json=f, headers=admin_a).status_code == 201
    for source, unit, amount, day in [
        ("electricity", "kWh", "1000", "2024-11-15"),
        ("electricity", "kWh", "1000", "2024-12-31"),
        ("electricity", "kWh", "1000", "2025-01-01"),
        ("fuel", "L", "100", "2025-02-10"),
        ("electricity", "kWh", "200", "2025-04-01"),
    ]:
        r = client.post("/records", headers=admin_a, json={
            "company_id": c["id"], "source": source, "unit": unit,
            "activity_amount": amount, "date": day,
        })
        assert r.status_code == 201, r.text
    return c


def summary(client, headers, company_id, **params):
    response = client.get(f"/companies/{company_id}/summary", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def buckets(body):
    return {b["key"]: Decimal(b["kg_co2e"]) for b in body["breakdown"]}


def test_total_over_all_dates(client, admin_a, company):
    body = summary(client, admin_a, company["id"])
    assert Decimal(body["total_kg_co2e"]) == Decimal("1470")  # 400+400+350+250+70
    assert body["record_count"] == 5


def test_group_by_month_spans_factor_versions(client, admin_a, company):
    body = summary(client, admin_a, company["id"], group_by="month")
    assert buckets(body) == {
        "2024-11": Decimal("400"),
        "2024-12": Decimal("400"),  # facteur v1 (0.40)
        "2025-01": Decimal("350"),  # facteur v2 (0.35)
        "2025-02": Decimal("250"),
        "2025-04": Decimal("70"),
    }
    assert [b["key"] for b in body["breakdown"]] == sorted(buckets(body))  # trié


def test_group_by_quarter(client, admin_a, company):
    body = summary(client, admin_a, company["id"], group_by="quarter")
    assert buckets(body) == {
        "2024-Q4": Decimal("800"),
        "2025-Q1": Decimal("600"),  # 350 + 250
        "2025-Q2": Decimal("70"),
    }


def test_group_by_source(client, admin_a, company):
    body = summary(client, admin_a, company["id"], group_by="source")
    assert buckets(body) == {"electricity": Decimal("1220"), "fuel": Decimal("250")}


def test_boundary_dates_are_inclusive(client, admin_a, company):
    # from = to = 2024-12-31 : seul le record de ce jour-là
    body = summary(client, admin_a, company["id"], **{"from": "2024-12-31", "to": "2024-12-31"})
    assert Decimal(body["total_kg_co2e"]) == Decimal("400")
    assert body["record_count"] == 1


def test_range_crossing_factor_change(client, admin_a, company):
    body = summary(client, admin_a, company["id"], **{"from": "2024-12-31", "to": "2025-01-01"})
    assert Decimal(body["total_kg_co2e"]) == Decimal("750")  # 400 (v1) + 350 (v2)


def test_empty_range_returns_zero(client, admin_a, company):
    body = summary(client, admin_a, company["id"], **{"from": "2030-01-01", "to": "2030-12-31"})
    assert Decimal(body["total_kg_co2e"]) == 0
    assert body["record_count"] == 0
    assert body["breakdown"] == []


def test_company_without_records_returns_zero(client, admin_a):
    c = client.post("/companies", json={"name": "Vide", "industry": "x"}, headers=admin_a).json()
    body = summary(client, admin_a, c["id"])
    assert Decimal(body["total_kg_co2e"]) == 0
    assert body["breakdown"] == []


def test_from_after_to_returns_422(client, admin_a, company):
    response = client.get(
        f"/companies/{company['id']}/summary",
        params={"from": "2025-01-01", "to": "2024-01-01"}, headers=admin_a,
    )
    assert response.status_code == 422


def test_invalid_group_by_returns_422(client, admin_a, company):
    response = client.get(
        f"/companies/{company['id']}/summary", params={"group_by": "week"}, headers=admin_a
    )
    assert response.status_code == 422


def test_summary_of_other_org_company_returns_404(client, admin_b, company):
    response = client.get(f"/companies/{company['id']}/summary", headers=admin_b)
    assert response.status_code == 404


def test_viewer_can_read_summary(client, auth_headers, company):
    viewer = auth_headers("viewer@a.io", role="viewer")
    assert client.get(f"/companies/{company['id']}/summary", headers=viewer).status_code == 200