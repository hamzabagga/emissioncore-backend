"""Entrées hors limites, échecs du worker et accès concurrents : jamais de 500, jamais de
données incohérentes."""
import base64
import json
import threading
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app import worker
from app.db.session import SessionLocal
from app.models import EmissionFactor, User
from app.services import imports
from app.worker import run_once

HEADER = "source,unit,activity_amount,date\n"
HUGE_ID = 99999999999  # > 2^31 - 1 : hors des limites d'une colonne integer


@pytest.fixture
def admin_a(auth_headers):
    return auth_headers("admin@a.io", role="admin", org_name="OrgA")


@pytest.fixture
def company(client, admin_a):
    c = client.post("/companies", json={"name": "Usine", "industry": "x"}, headers=admin_a).json()
    response = client.post("/emission-factors", headers=admin_a, json={
        "source": "electricity", "unit": "kWh", "kg_co2e_per_unit": "0.40",
        "valid_from": "2024-01-01", "valid_to": None,
    })
    assert response.status_code == 201, response.text
    return c


def run_worker_until_idle():
    with SessionLocal() as db:
        while run_once(db):
            pass


def cursor(value, row_id=1):
    return base64.urlsafe_b64encode(json.dumps({"v": value, "id": row_id}).encode()).decode()


def upload(client, headers, company_id, csv_text):
    return client.post(
        f"/companies/{company_id}/records/import",
        files={"file": ("data.csv", csv_text.encode(), "text/csv")},
        headers=headers,
    )


# ---------- Valeurs hors limites : 422, pas 500 ----------

@pytest.mark.parametrize("method,path", [
    ("get", f"/companies/{HUGE_ID}"),
    ("get", f"/companies/{HUGE_ID}/summary"),
    ("get", f"/users/{HUGE_ID}"),
    ("get", f"/records/{HUGE_ID}"),
    ("get", f"/emission-factors/{HUGE_ID}"),
    ("get", f"/jobs/{HUGE_ID}"),
    ("get", f"/imports/{HUGE_ID}"),
    ("get", f"/records?company_id={HUGE_ID}"),
    ("get", f"/audit?entity_id={HUGE_ID}"),
    ("delete", f"/records/{HUGE_ID}"),
])
def test_id_out_of_integer_range_returns_422(client, admin_a, method, path):
    response = getattr(client, method)(path, headers=admin_a)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_value"


def test_record_with_out_of_range_company_id_returns_422(client, admin_a):
    response = client.post("/records", headers=admin_a, json={
        "company_id": HUGE_ID, "source": "electricity", "unit": "kWh",
        "activity_amount": "1", "date": "2024-03-01",
    })
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("method,path,kwargs", [
    ("post", "/companies", {"json": {"name": "a\u0000b", "industry": "x"}}),
    ("get", "/companies", {"params": {"industry": "a\x00"}}),
    ("get", "/emission-factors", {"params": {"unit": "a\x00"}}),
])
def test_nul_character_in_text_returns_422(client, admin_a, method, path, kwargs):
    response = getattr(client, method)(path, headers=admin_a, **kwargs)
    assert response.status_code == 422, response.text


def test_nul_character_in_record_unit_returns_422(client, admin_a, company):
    response = client.post("/records", headers=admin_a, json={
        "company_id": company["id"], "source": "electricity", "unit": "kWh\u0000",
        "activity_amount": "1", "date": "2024-03-01",
    })
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("path,sort,value,row_id", [
    ("/records", "kg_co2e", "1e1000000", 1),
    ("/records", "kg_co2e", "-1e200000", 1),
    ("/records", "kg_co2e", "NaN", 1),
    ("/records", "kg_co2e", "Infinity", 1),
    ("/records", "id", 1, 10**40),
    ("/companies", "id", 1, 10**40),
    ("/companies", "id", 1, "1"),
])
def test_forged_cursor_out_of_range_returns_422(client, admin_a, path, sort, value, row_id):
    # (sort dans params : httpx ignore la query string de l'URL quand params est donné)
    response = client.get(
        path, params={"sort": sort, "cursor": cursor(value, row_id)}, headers=admin_a
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_cursor"


# ---------- Worker ----------

def test_nul_character_in_csv_is_a_row_error(client, admin_a, company):
    # avant la correction : le message d'erreur contenait \x00, refusé par le JSONB
    # -> le job restait "processing" pour toujours
    csv_text = HEADER + "electricity,kWh,100,2024-03-01\nelectricity,kWh,1,2024-03-0\x001\n"
    job = upload(client, admin_a, company["id"], csv_text).json()
    run_worker_until_idle()
    job = client.get(f"/imports/{job['id']}", headers=admin_a).json()

    assert job["status"] == "completed"
    assert (job["success_rows"], job["error_rows"]) == (1, 1)
    assert "\x00" not in job["errors"][0]["error"]


def test_job_failing_on_commit_is_marked_failed_not_stuck(client, admin_a, company, monkeypatch):
    def broken_handler(db, job):
        job.error_message = "\x00"  # refusé par PostgreSQL au flush : l'objet job est expiré
        db.commit()

    monkeypatch.setitem(worker.HANDLERS, "import", broken_handler)
    job = upload(client, admin_a, company["id"], HEADER + "electricity,kWh,1,2024-03-01\n").json()
    run_worker_until_idle()

    job = client.get(f"/imports/{job['id']}", headers=admin_a).json()
    assert job["status"] == "failed"
    assert job["error_message"] == "Internal error while processing the job"


def test_factor_changed_during_import_schedules_a_new_recalculation(
    client, admin_a, company, monkeypatch
):
    run_worker_until_idle()  # recalcul automatique de la création du facteur
    original_init = imports._FactorLookup.__init__

    def init_then_factor_changes(self, db, organization_id):
        original_init(self, db, organization_id)
        # un autre admin corrige le facteur pendant que l'import tourne
        with SessionLocal() as other:
            other.execute(update(EmissionFactor).values(kg_co2e_per_unit=Decimal("0.50")))
            other.commit()

    monkeypatch.setattr(imports._FactorLookup, "__init__", init_then_factor_changes)
    job = upload(client, admin_a, company["id"], HEADER + "electricity,kWh,1000,2024-03-01\n")
    run_worker_until_idle()

    assert client.get(f"/imports/{job.json()['id']}", headers=admin_a).json()["status"] == "completed"
    records = client.get("/records", headers=admin_a).json()["items"]
    assert Decimal(records[0]["kg_co2e"]) == Decimal("500")  # et non 400 (valeur périmée)


# ---------- Suppression d'une company : les records supprimés sont audités ----------

def test_deleting_company_audits_its_deleted_records(client, admin_a, company):
    record = client.post("/records", headers=admin_a, json={
        "company_id": company["id"], "source": "electricity", "unit": "kWh",
        "activity_amount": "1000", "date": "2024-03-01",
    }).json()
    assert client.delete(f"/companies/{company['id']}", headers=admin_a).status_code == 204

    logs = client.get(
        "/audit", params={"entity": "emission_record", "entity_id": record["id"]}, headers=admin_a
    ).json()["items"]
    assert [log["action"] for log in logs] == ["delete", "create"]
    # même format que l'entrée "create" (valeurs normalisées)
    assert logs[0]["changes"] == logs[1]["changes"]


# ---------- Admins qui se rétrogradent l'un l'autre en même temps ----------

def test_two_admins_demoting_each_other_concurrently_keep_one_admin(client, auth_headers):
    admin_1 = auth_headers("admin1@a.io", role="admin")
    admin_2 = auth_headers("admin2@a.io", role="admin")
    id_1 = client.get("/auth/me", headers=admin_1).json()["id"]
    id_2 = client.get("/auth/me", headers=admin_2).json()["id"]
    barrier = threading.Barrier(2)
    statuses = []

    def demote(headers, target_id):
        barrier.wait()
        response = client.patch(f"/users/{target_id}", json={"role": "viewer"}, headers=headers)
        statuses.append(response.status_code)

    threads = [
        threading.Thread(target=demote, args=(admin_1, id_2)),
        threading.Thread(target=demote, args=(admin_2, id_1)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(statuses) == [200, 403]
    with SessionLocal() as db:
        admins = db.scalar(select(func.count()).select_from(User).where(User.role == "admin"))
    assert admins == 1

