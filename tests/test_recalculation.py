from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models import Job
from app.worker import run_once


@pytest.fixture
def admin_a(auth_headers):
    return auth_headers("admin@a.io", role="admin", org_name="OrgA")


@pytest.fixture
def admin_b(auth_headers):
    return auth_headers("admin@b.io", role="admin", org_name="OrgB")


@pytest.fixture
def setup(client, admin_a):
    """1 facteur électricité (0.40) et 2 records de 1000 kWh -> 400 kg chacun."""
    company = client.post(
        "/companies", json={"name": "Usine", "industry": "x"}, headers=admin_a
    ).json()
    factor = client.post("/emission-factors", headers=admin_a, json={
        "source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.40",
        "valid_from": "2024-01-01", "valid_to": None,
    }).json()
    records = [
        client.post("/records", headers=admin_a, json={
            "company_id": company["id"], "source": "electricity", "unit": "kWh",
            "activity_amount": "1000", "date": day,
        }).json()
        for day in ["2024-03-01", "2025-03-01"]
    ]
    return {"company": company, "factor": factor, "records": records}


def run_worker_until_idle():
    with SessionLocal() as db:
        while run_once(db):
            pass


def recalculate(client, headers):
    response = client.post("/emission-factors/recalculate", headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending"
    run_worker_until_idle()
    return client.get(f"/jobs/{response.json()['id']}", headers=headers).json()


def kg_by_date(client, headers):
    items = client.get("/records", headers=headers).json()["items"]
    return {r["date"]: Decimal(r["kg_co2e"]) for r in items}


def test_correcting_a_factor_then_recalculating_updates_records(client, admin_a, setup):
    # Correction : le facteur valait en réalité 0.42
    client.patch(
        f"/emission-factors/{setup['factor']['id']}",
        json={"kg_co2e_per_unit": "0.42"}, headers=admin_a,
    )
    # Tant qu'on n'a pas recalculé, les records gardent l'ancienne valeur
    assert kg_by_date(client, admin_a)["2024-03-01"] == Decimal("400")

    job = recalculate(client, admin_a)

    assert job["status"] == "completed"
    assert job["success_rows"] == 2
    assert kg_by_date(client, admin_a) == {
        "2024-03-01": Decimal("420"), "2025-03-01": Decimal("420"),
    }


def test_new_factor_version_reassigns_records_after_its_start_date(client, admin_a, setup):
    # On coupe le facteur v1 au 2025-01-01 et on ajoute une v2 à 0.30
    client.patch(
        f"/emission-factors/{setup['factor']['id']}",
        json={"valid_to": "2025-01-01"}, headers=admin_a,
    )
    v2 = client.post("/emission-factors", headers=admin_a, json={
        "source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.30",
        "valid_from": "2025-01-01", "valid_to": None,
    }).json()

    job = recalculate(client, admin_a)

    assert job["success_rows"] == 1  # seul le record de 2025 change
    items = {r["date"]: r for r in client.get("/records", headers=admin_a).json()["items"]}
    assert Decimal(items["2024-03-01"]["kg_co2e"]) == Decimal("400")
    assert Decimal(items["2025-03-01"]["kg_co2e"]) == Decimal("300")
    assert items["2025-03-01"]["factor_id"] == v2["id"]


def test_recalculation_is_idempotent(client, admin_a, setup):
    client.patch(
        f"/emission-factors/{setup['factor']['id']}",
        json={"kg_co2e_per_unit": "0.42"}, headers=admin_a,
    )
    first = recalculate(client, admin_a)
    second = recalculate(client, admin_a)

    assert first["success_rows"] == 2
    assert second["success_rows"] == 0  # rien ne change au 2e passage
    assert kg_by_date(client, admin_a)["2024-03-01"] == Decimal("420")


def test_records_without_valid_factor_are_reported_not_changed(client, admin_a, setup):
    # La période du facteur ne couvre plus le record de 2024
    client.patch(
        f"/emission-factors/{setup['factor']['id']}",
        json={"valid_from": "2025-01-01"}, headers=admin_a,
    )
    job = recalculate(client, admin_a)

    assert job["error_rows"] == 1
    assert "no valid emission factor" in job["error_message"]
    assert kg_by_date(client, admin_a)["2024-03-01"] == Decimal("400")  # inchangé


def test_recalculation_does_not_touch_other_organizations(client, admin_a, admin_b, setup):
    # OrgB a ses propres données
    company_b = client.post(
        "/companies", json={"name": "B", "industry": "x"}, headers=admin_b
    ).json()
    factor_b = client.post("/emission-factors", headers=admin_b, json={
        "source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.50",
        "valid_from": "2024-01-01", "valid_to": None,
    }).json()
    client.post("/records", headers=admin_b, json={
        "company_id": company_b["id"], "source": "electricity", "unit": "kWh",
        "activity_amount": "1000", "date": "2024-03-01",
    })
    run_worker_until_idle()  # jobs automatiques déjà en attente
    # B corrige son facteur (recalcul automatique de B en attente), A lance aussi un recalcul
    client.patch(
        f"/emission-factors/{factor_b['id']}", json={"kg_co2e_per_unit": "0.90"}, headers=admin_b
    )
    job_b = client.post("/emission-factors/recalculate", headers=admin_b).json()
    job_a = client.post("/emission-factors/recalculate", headers=admin_a).json()
    assert job_a["id"] != job_b["id"]
    run_worker_until_idle()

    job_a = client.get(f"/jobs/{job_a['id']}", headers=admin_a).json()
    job_b = client.get(f"/jobs/{job_b['id']}", headers=admin_b).json()
    assert job_a["success_rows"] == 0  # le job de A ne touche pas aux records de B
    assert job_b["success_rows"] == 1
    assert kg_by_date(client, admin_b)["2024-03-01"] == Decimal("900")
    assert kg_by_date(client, admin_a)["2024-03-01"] == Decimal("400")  # A inchangé


def test_job_of_other_org_returns_404(client, admin_a, admin_b, setup):
    job_id = client.post("/emission-factors/recalculate", headers=admin_a).json()["id"]
    assert client.get(f"/jobs/{job_id}", headers=admin_b).status_code == 404


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_only_admin_can_trigger_recalculation(client, auth_headers, role):
    headers = auth_headers(f"{role}@a.io", role=role)
    assert client.post("/emission-factors/recalculate", headers=headers).status_code == 403


def test_record_that_would_overflow_is_reported_not_a_failed_job(client, admin_a, setup):
    # Facteur corrigé à une valeur énorme : 1000 kWh x 1e12 dépasse Numeric(20, 6).
    # Avant la correction : tout l'UPDATE échouait -> job "failed", aucun record recalculé.
    client.patch(
        f"/emission-factors/{setup['factor']['id']}",
        json={"valid_to": "2025-01-01"}, headers=admin_a,
    )
    client.post("/emission-factors", headers=admin_a, json={
        "source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "999999999999",
        "valid_from": "2025-01-01", "valid_to": None,
    })
    job = recalculate(client, admin_a)

    assert job["status"] == "completed"
    assert job["error_rows"] == 1
    assert "too large" in job["error_message"]
    assert kg_by_date(client, admin_a) == {
        "2024-03-01": Decimal("400"),  # inchangé (même facteur)
        "2025-03-01": Decimal("400"),  # laissé inchangé, signalé
    }


# ---------- Recalcul automatique ----------

def test_factor_update_schedules_recalculation_automatically(client, admin_a, setup):
    client.patch(
        f"/emission-factors/{setup['factor']['id']}",
        json={"kg_co2e_per_unit": "0.42"}, headers=admin_a,
    )
    run_worker_until_idle()  # sans appeler POST /emission-factors/recalculate

    assert kg_by_date(client, admin_a) == {
        "2024-03-01": Decimal("420"), "2025-03-01": Decimal("420"),
    }


def test_new_factor_version_is_applied_automatically(client, admin_a, setup):
    client.patch(
        f"/emission-factors/{setup['factor']['id']}",
        json={"valid_to": "2025-01-01"}, headers=admin_a,
    )
    client.post("/emission-factors", headers=admin_a, json={
        "source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.30",
        "valid_from": "2025-01-01", "valid_to": None,
    })
    run_worker_until_idle()

    assert kg_by_date(client, admin_a) == {
        "2024-03-01": Decimal("400"), "2025-03-01": Decimal("300"),
    }


def test_pending_recalculation_job_is_reused(client, admin_a, setup):
    # plusieurs modifications avant le passage du worker -> un seul job en attente
    for value in ["0.41", "0.42"]:
        client.patch(
            f"/emission-factors/{setup['factor']['id']}",
            json={"kg_co2e_per_unit": value}, headers=admin_a,
        )
    first = client.post("/emission-factors/recalculate", headers=admin_a).json()
    second = client.post("/emission-factors/recalculate", headers=admin_a).json()
    assert first["id"] == second["id"]
    with SessionLocal() as db:
        pending = db.scalar(
            select(func.count()).select_from(Job)
            .where(Job.type == "recalculation", Job.status == "pending")
        )
    assert pending == 1


def test_noop_factor_update_does_not_schedule_recalculation(client, admin_a, setup):
    run_worker_until_idle()
    client.patch(
        f"/emission-factors/{setup['factor']['id']}",
        json={"kg_co2e_per_unit": "0.40"}, headers=admin_a,  # même valeur
    )
    with SessionLocal() as db:
        pending = db.scalar(select(func.count()).select_from(Job).where(Job.status == "pending"))
    assert pending == 0
