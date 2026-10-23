import os

from sqlalchemy.engine import make_url

# IMPORTANT : AVANT d'importer l'application. Ainsi TOUT le code (app, SessionLocal,
# Alembic) pointe vers la base de test : on ne touche jamais aux données de dev.
_dev_url = make_url(
    os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://emission:emission@localhost:5432/emission"
    )
)
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", _dev_url.set(database="emission_test").render_as_string(False)
)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Organization, User  # noqa: E402

TEST_PASSWORD = "password123"
TABLES = "emission_records, jobs, emission_factors, companies, users, organizations"


def _create_test_database_if_missing() -> None:
    url = make_url(os.environ["DATABASE_URL"])
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Une fois : crée la base de test et applique les VRAIES migrations Alembic
    (contrainte d'exclusion comprise), pas un simple create_all."""
    _create_test_database_if_missing()
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables():
    """Avant chaque test : tables vides -> tests indépendants."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db_session_factory():
    return SessionLocal


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_user():
    """Crée un utilisateur (et son organisation si besoin) directement en base."""

    def _make_user(email: str, role: str = "admin", org_name: str = "OrgA") -> User:
        with SessionLocal() as db:
            org = db.query(Organization).filter_by(name=org_name).one_or_none()
            if org is None:
                org = Organization(name=org_name)
                db.add(org)
                db.flush()
            user = User(
                email=email.lower(),
                role=role,
                organization_id=org.id,
                password_hash=hash_password(TEST_PASSWORD),
            )
            db.add(user)
            db.commit()
            return user

    return _make_user


@pytest.fixture
def login(client):
    def _login(email: str, password: str = TEST_PASSWORD) -> dict:
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return response.json()

    return _login


@pytest.fixture
def auth_headers(make_user, login):
    """Crée un utilisateur, le connecte, renvoie l'en-tête Authorization."""

    def _auth_headers(email: str, role: str = "admin", org_name: str = "OrgA") -> dict:
        make_user(email, role=role, org_name=org_name)
        token = login(email)["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _auth_headers
