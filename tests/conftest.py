import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Organization, User

TEST_PASSWORD = "password123"

# Base dédiée aux tests : on ne touche jamais aux données de dev
TEST_DATABASE_URL = make_url(settings.database_url).set(database="emission_test")


def _create_test_database() -> None:
    admin_engine = create_engine(
        TEST_DATABASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin_engine.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": TEST_DATABASE_URL.database},
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE_URL.database}"'))
    admin_engine.dispose()


@pytest.fixture(scope="session")
def engine():
    _create_test_database()
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session_factory(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    # Chaque test repart d'une base vide
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client(db_session_factory):
    def override_get_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(db_session_factory):
    """Crée un utilisateur (et son organisation si besoin) directement en base."""

    def _make_user(email: str, role: str = "admin", org_name: str = "OrgA") -> User:
        with db_session_factory() as db:
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
