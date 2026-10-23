import threading
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models import EmissionRecord, Job
from app.services.imports import create_import_job
from app.worker import run_once

HEADER = "source,unit,activity_amount,date\n"


@pytest.fixture
def admin_a(auth_headers):
    return auth_headers("admin@a.io", role="admin", org_name="OrgA")


@pytest.fixture
def admin_b(auth_headers):
    return auth_headers("admin@b.io", role="admin", org_name="OrgB")


@pytest.fixture
def company(client, admin_a):
    c = client.post("/companies", json={"name": "Usine", "industry": "x"}, headers=admin_a).json()
    for f in [
        {"source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.40",
         "valid_from": "2024-01-01", "valid_to": "2025-01-01"},
        {"source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.35",
         "valid_from": "2025-01-01", "valid_to": None},
    ]:
        assert client.post("/emission-factors", json=f, headers=admin_a).status_code == 201
    return c


def upload(client, headers, company_id, csv_text, name="data.csv"):
    return client.post(
        f"/companies/{company_id}/records/import",
        files={"file": (name, csv_text.encode(), "text/csv")},
        headers=headers,
    )


def run_worker_until_idle():
    with SessionLocal() as db:
        while run_once(db):
            pass


def count_records():
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(EmissionRecord))


# ---------- Flux complet ----------

def test_import_is_async_then_completed(client, admin_a, company):
    csv_text = HEADER + "electricity,kWh,1000,2024-06-01\nelectricity,kWh,1000,2025-06-01\n"
    response = upload(client, admin_a, company["id"], csv_text)
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "pending"   # l'API répond tout de suite, rien n'est encore importé
    assert count_records() == 0

    run_worker_until_idle()

    job = client.get(f"/imports/{job['id']}", headers=admin_a).json()
    assert job["status"] == "completed"
    assert (job["total_rows"], job["success_rows"], job["error_rows"]) == (2, 2, 0)

    records = client.get(
        "/records", params={"sort": "date", "order": "asc"}, headers=admin_a
    ).json()["items"]
    # le bon facteur selon la date : 0.40 en 2024, 0.35 en 2025
    assert [Decimal(r["kg_co2e"]) for r in records] == [Decimal("400"), Decimal("350")]


def test_invalid_rows_are_reported_and_do_not_fail_the_import(client, admin_a, company):
    csv_text = HEADER + "\n".join([
        "electricity,kWh,100,2024-03-01",   # ligne 2 : OK
        "gas,kWh,100,2024-03-01",           # ligne 3 : source inconnue
        "electricity,kWh,abc,2024-03-01",   # ligne 4 : montant invalide
        "electricity,kWh,-5,2024-03-01",    # ligne 5 : montant négatif
        "electricity,kWh,100,2024-13-45",   # ligne 6 : date invalide
        "electricity,kWh,100,2023-03-01",   # ligne 7 : aucun facteur valide
        "electricity,MWh,100,2024-03-01",   # ligne 8 : aucun facteur pour cette unité
        "electricity,kWh,200,2024-03-02",   # ligne 9 : OK
    ]) + "\n"
    job = upload(client, admin_a, company["id"], csv_text).json()
    run_worker_until_idle()
    job = client.get(f"/imports/{job['id']}", headers=admin_a).json()

    assert job["status"] == "completed"
    assert (job["success_rows"], job["error_rows"]) == (2, 6)
    assert [e["row"] for e in job["errors"]] == [3, 4, 5, 6, 7, 8]
    assert "source" in job["errors"][0]["error"]
    assert count_records() == 2


def test_missing_columns_rejected_immediately(client, admin_a, company):
    response = upload(client, admin_a, company["id"], "source,unit,date\nelectricity,kWh,2024-01-01\n")
    assert response.status_code == 422
    assert response.json()["error"]["details"]["missing"] == ["activity_amount"]


# ---------- Idempotence ----------

def test_reuploading_same_file_returns_same_job_and_no_duplicates(client, admin_a, company):
    csv_text = HEADER + "electricity,kWh,1000,2024-06-01\n"
    first = upload(client, admin_a, company["id"], csv_text)
    run_worker_until_idle()
    second = upload(client, admin_a, company["id"], csv_text, name="other-name.csv")

    assert first.status_code == 202
    assert second.status_code == 200          # pas de nouveau job
    assert second.json()["id"] == first.json()["id"]
    run_worker_until_idle()
    assert count_records() == 1


def test_rerunning_a_job_after_crash_creates_no_duplicates(client, admin_a, company):
    csv_text = HEADER + "".join(f"electricity,kWh,{i},2024-06-01\n" for i in range(1, 51))
    job_id = upload(client, admin_a, company["id"], csv_text).json()["id"]
    run_worker_until_idle()
    assert count_records() == 50

    # Simule un worker mort en plein traitement : le job repart de zéro
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.status = "pending"
        job.file_content = csv_text.encode()
        db.commit()
    run_worker_until_idle()

    assert count_records() == 50  # ON CONFLICT DO NOTHING : aucun doublon


def test_concurrent_uploads_of_same_file_create_one_job(client, admin_a, company):
    """Avancé #3 : deux uploads simultanés du même fichier -> un seul job."""
    user = client.get("/auth/me", headers=admin_a).json()
    content = (HEADER + "electricity,kWh,1,2024-06-01\n").encode()
    barrier = threading.Barrier(2)
    results = []

    def do_upload():
        with SessionLocal() as db:
            barrier.wait()  # les deux threads partent en même temps
            job, created = create_import_job(
                db, user["organization_id"], company["id"], user["id"], content
            )
            results.append((job.id, created))

    threads = [threading.Thread(target=do_upload) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len({job_id for job_id, _ in results}) == 1      # le même job
    assert sorted(created for _, created in results) == [False, True]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1


# ---------- Volume ----------

def test_large_import_uses_batches(client, admin_a, company):
    rows = 12_000  # > BATCH_SIZE (5 000) -> plusieurs lots
    csv_text = HEADER + "electricity,kWh,1,2024-06-01\n" * rows
    job = upload(client, admin_a, company["id"], csv_text).json()
    run_worker_until_idle()
    job = client.get(f"/imports/{job['id']}", headers=admin_a).json()
    assert job["success_rows"] == rows
    assert count_records() == rows


# ---------- Sécurité ----------

def test_cannot_import_into_other_org_company(client, admin_b, company):
    response = upload(client, admin_b, company["id"], HEADER + "electricity,kWh,1,2024-06-01\n")
    assert response.status_code == 404


def test_import_status_of_other_org_returns_404(client, admin_a, admin_b, company):
    job = upload(client, admin_a, company["id"], HEADER + "electricity,kWh,1,2024-06-01\n").json()
    assert client.get(f"/imports/{job['id']}", headers=admin_b).status_code == 404


def test_viewer_cannot_import(client, auth_headers, company):
    viewer = auth_headers("viewer@a.io", role="viewer")
    response = upload(client, viewer, company["id"], HEADER + "electricity,kWh,1,2024-06-01\n")
    assert response.status_code == 403
