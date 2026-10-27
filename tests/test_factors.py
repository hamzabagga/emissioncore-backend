import threading
import time
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models import EmissionFactor


@pytest.fixture
def admin_a(auth_headers):
    return auth_headers("admin@a.io", role="admin", org_name="OrgA")


@pytest.fixture
def admin_b(auth_headers):
    return auth_headers("admin@b.io", role="admin", org_name="OrgB")


def factor_payload(valid_from, valid_to=None, value="0.40", unit="kWh", source="electricity"):
    return {
        "source": source,
        "unit": unit,
        "kg_co2e_per_unit": value,
        "valid_from": valid_from,
        "valid_to": valid_to,
    }


def create_factor(client, headers, *args, **kwargs):
    response = client.post("/emission-factors", json=factor_payload(*args, **kwargs), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------- Versionnement / chevauchement ----------

def test_create_factor(client, admin_a):
    factor = create_factor(client, admin_a, "2024-01-01", "2025-01-01")
    assert factor["source"] == "electricity"
    assert factor["valid_to"] == "2025-01-01"


def test_overlapping_factor_returns_409_with_conflicting_id(client, admin_a):
    first = create_factor(client, admin_a, "2024-01-01", "2025-01-01")
    response = client.post(
        "/emission-factors", json=factor_payload("2024-06-01", None, "0.35"), headers=admin_a
    )
    assert response.status_code == 409
    assert response.json()["error"]["details"]["conflicting_factor_id"] == first["id"]


def test_adjacent_periods_do_not_overlap_because_valid_to_is_exclusive(client, admin_a):
    create_factor(client, admin_a, "2024-01-01", "2025-01-01", "0.40")
    create_factor(client, admin_a, "2025-01-01", None, "0.35")  # commence le jour où v1 finit


def test_open_ended_factor_blocks_any_later_factor(client, admin_a):
    create_factor(client, admin_a, "2024-01-01", None)  # sans fin
    response = client.post(
        "/emission-factors", json=factor_payload("2030-01-01", None), headers=admin_a
    )
    assert response.status_code == 409


def test_same_period_allowed_for_different_unit_or_source(client, admin_a):
    create_factor(client, admin_a, "2024-01-01", None, unit="kWh")
    create_factor(client, admin_a, "2024-01-01", None, unit="MWh")
    create_factor(client, admin_a, "2024-01-01", None, unit="kWh", source="other")


def test_valid_to_before_valid_from_returns_422(client, admin_a):
    response = client.post(
        "/emission-factors", json=factor_payload("2025-01-01", "2024-01-01"), headers=admin_a
    )
    assert response.status_code == 422


def test_negative_factor_value_returns_422(client, admin_a):
    response = client.post(
        "/emission-factors", json=factor_payload("2024-01-01", None, "-1"), headers=admin_a
    )
    assert response.status_code == 422


def test_update_that_creates_overlap_returns_409(client, admin_a):
    create_factor(client, admin_a, "2024-01-01", "2025-01-01")
    second = create_factor(client, admin_a, "2025-01-01", None)
    response = client.patch(
        f"/emission-factors/{second['id']}", json={"valid_from": "2024-06-01"}, headers=admin_a
    )
    assert response.status_code == 409


def test_update_value_without_changing_period_is_allowed(client, admin_a):
    factor = create_factor(client, admin_a, "2024-01-01", None, "0.40")
    response = client.patch(
        f"/emission-factors/{factor['id']}", json={"kg_co2e_per_unit": "0.42"}, headers=admin_a
    )
    assert response.status_code == 200
    assert response.json()["kg_co2e_per_unit"] == "0.420000"


def test_same_period_allowed_in_another_organization(client, admin_a, admin_b):
    create_factor(client, admin_a, "2024-01-01", None)
    create_factor(client, admin_b, "2024-01-01", None)  # pas de conflit entre organisations


# ---------- Concurrence (avancé #3) ----------

def test_database_blocks_concurrent_overlapping_factors(make_user):
    """Deux transactions simultanées passent toutes les deux la vérification Python,
    mais la contrainte d'exclusion PostgreSQL n'en laisse passer qu'une."""
    org_id = make_user("admin@a.io").organization_id
    first, second = SessionLocal(), SessionLocal()
    result = {}
    try:
        first.add(EmissionFactor(
            organization_id=org_id, source="electricity", unit="kWh",
            kg_co2e_per_unit="0.40", valid_from=date(2024, 1, 1), valid_to=None,
        ))
        first.flush()  # ligne écrite mais PAS encore validée (commit)

        def insert_overlapping():
            try:
                second.add(EmissionFactor(
                    organization_id=org_id, source="electricity", unit="kWh",
                    kg_co2e_per_unit="0.35", valid_from=date(2024, 6, 1), valid_to=None,
                ))
                second.commit()  # bloque en attendant la 1re transaction
                result["second_committed"] = True
            except IntegrityError:
                second.rollback()
                result["second_committed"] = False

        thread = threading.Thread(target=insert_overlapping)
        thread.start()
        time.sleep(0.5)  # la 2e transaction attend maintenant le verrou
        first.commit()
        thread.join(timeout=10)
    finally:
        first.close()
        second.close()

    assert result["second_committed"] is False
    with SessionLocal() as check:
        assert check.scalar(select(func.count()).select_from(EmissionFactor)) == 1


# ---------- Isolation ----------

def test_factors_of_other_org_are_invisible(client, admin_a, admin_b):
    factor_a = create_factor(client, admin_a, "2024-01-01", None)

    assert client.get(f"/emission-factors/{factor_a['id']}", headers=admin_b).status_code == 404
    assert client.patch(
        f"/emission-factors/{factor_a['id']}", json={"kg_co2e_per_unit": "9"}, headers=admin_b
    ).status_code == 404
    assert client.delete(f"/emission-factors/{factor_a['id']}", headers=admin_b).status_code == 404
    assert client.get("/emission-factors", headers=admin_b).json()["items"] == []


# ---------- Rôles ----------

@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_only_admin_can_manage_factors(client, auth_headers, role):
    headers = auth_headers(f"{role}@a.io", role=role)
    response = client.post(
        "/emission-factors", json=factor_payload("2024-01-01"), headers=headers
    )
    assert response.status_code == 403


def test_viewer_can_list_factors(client, admin_a, auth_headers):
    create_factor(client, admin_a, "2024-01-01", None)
    viewer = auth_headers("viewer@a.io", role="viewer")
    response = client.get("/emission-factors", headers=viewer)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1

# ---------- Valeurs null dans un PATCH ----------

def test_update_with_null_value_returns_422_and_keeps_factor(client, admin_a):
    factor = create_factor(client, admin_a, "2024-01-01", "2025-01-01")
    response = client.patch(
        f"/emission-factors/{factor['id']}", json={"kg_co2e_per_unit": None}, headers=admin_a
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    # le facteur n'a pas changé
    unchanged = client.get(f"/emission-factors/{factor['id']}", headers=admin_a).json()
    assert unchanged["kg_co2e_per_unit"] == factor["kg_co2e_per_unit"]
    
    

def test_update_with_null_valid_from_returns_422(client, admin_a):
    factor = create_factor(client, admin_a, "2024-01-01", "2025-01-01")
    response = client.patch(
        f"/emission-factors/{factor['id']}", json={"valid_from": None}, headers=admin_a
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"

