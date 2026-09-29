"""Shared fixtures.

Tests run against a REAL PostgreSQL database (default: eve_test on localhost). The schema is built
by running the Alembic migrations, so the migrations themselves are tested, and the concurrency
tests exercise real row locks / unique indexes. Redis is replaced by fakeredis.
"""

import os

# Must be set before `app` is imported (settings are read at import time).
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://eve:eve@localhost:5432/eve_test"
)
os.environ.update(
    {
        "DATABASE_URL": TEST_DATABASE_URL,
        "ENVIRONMENT": "test",
        "JWT_SECRET_KEY": "test-secret-key-which-is-long-enough-1234567890",
        "RATE_LIMIT_ENABLED": "false",
        "WEBHOOK_SIGNATURE_REQUIRED": "false",
        "PAYMENT_ALLOW_SIMULATION_OVERRIDE": "true",
        "PAYMENT_SIMULATION_MODE": "success",
        "LOG_LEVEL": "WARNING",
    }
)

from collections.abc import Iterator  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from argon2 import PasswordHasher  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.core import security  # noqa: E402
from app.core.redis import get_redis  # noqa: E402
from app.db.database import SessionLocal, engine  # noqa: E402
from app.db.models import CentreTest, DiagnosticCentre, DiagnosticTest  # noqa: E402
from app.main import app  # noqa: E402
from tests.factories import signup_and_login  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _fast_password_hashing() -> None:
    """Cheap Argon2 parameters so the suite isn't dominated by hashing time."""
    security._hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)


def _ensure_test_database_exists() -> None:
    url = make_url(TEST_DATABASE_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    _ensure_test_database_exists()
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(_database: None) -> Iterator[None]:
    yield
    with engine.begin() as connection:
        tables = connection.scalars(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).all()
        connection.execute(text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"))


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


@pytest.fixture
def client(fake_redis: fakeredis.FakeRedis) -> Iterator[TestClient]:
    app.dependency_overrides[get_redis] = lambda: fake_redis
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    return signup_and_login(client, "alice@example.com")


@pytest.fixture
def other_headers(client: TestClient) -> dict[str, str]:
    return signup_and_login(client, "bob@example.com")


@pytest.fixture
def catalogue(db: Session) -> SimpleNamespace:
    """Two centres (one closed) and two tests with known prices."""
    alpha = DiagnosticCentre(name="Alpha Diagnostics", location="1 Test Street, Pune")
    beta = DiagnosticCentre(name="Beta Labs", location="2 Sample Road, Delhi")
    closed = DiagnosticCentre(name="Closed Labs", location="3 Old Lane", is_active=False)
    cbc = DiagnosticTest(name="CBC", description="Complete blood count")
    hba1c = DiagnosticTest(name="HbA1c", description="Blood sugar average")
    lipid = DiagnosticTest(name="Lipid Profile", description="Cholesterol")
    db.add_all([alpha, beta, closed, cbc, hba1c, lipid])
    db.flush()
    alpha_cbc = CentreTest(centre_id=alpha.id, test_id=cbc.id, price="350.00")
    alpha_hba1c = CentreTest(centre_id=alpha.id, test_id=hba1c.id, price="550.00")
    alpha_unavailable = CentreTest(
        centre_id=alpha.id, test_id=lipid.id, price="650.00", is_available=False
    )
    beta_cbc = CentreTest(centre_id=beta.id, test_id=cbc.id, price="300.00")
    closed_cbc = CentreTest(centre_id=closed.id, test_id=cbc.id, price="100.00")
    db.add_all([alpha_cbc, alpha_hba1c, alpha_unavailable, beta_cbc, closed_cbc])
    db.commit()
    return SimpleNamespace(
        alpha=alpha,
        beta=beta,
        closed=closed,
        cbc=cbc,
        hba1c=hba1c,
        lipid=lipid,
        alpha_cbc=alpha_cbc,
        alpha_hba1c=alpha_hba1c,
        alpha_unavailable=alpha_unavailable,
        beta_cbc=beta_cbc,
        closed_cbc=closed_cbc,
    )
