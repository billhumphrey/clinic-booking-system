import os
from datetime import time, timedelta


os.environ.setdefault("CLINIC_TIMEZONE", "UTC")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app import models
from app.utils import utc_now

# Respect DATABASE_URL if set (CI runs this against a real Postgres service
# container to match production). Otherwise use an in-memory SQLite database
# via StaticPool so every test gets a fresh schema and never leaves a stale file
# or journal that could lock the suite.
TEST_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///:memory:")
use_sqlite = TEST_DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if use_sqlite else {}
poolclass = StaticPool if use_sqlite else None

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args=connect_args,
    poolclass=poolclass,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Create the schema once before the test session, tear it down at the end."""
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_and_client():
    """
    Open a single database transaction for one test. Both the test fixtures and
    the FastAPI app share the same session, and the transaction is rolled back at
    the end so each test starts clean. This avoids expensive drop_all/create_all
    cycles and the Postgres lock issues that can make the CI suite hang.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield session, client

    app.dependency_overrides.clear()
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_and_client):
    return db_and_client[1]


@pytest.fixture
def db_session(db_and_client):
    return db_and_client[0]


@pytest.fixture
def doctor(db_session):
    doc = models.Doctor(name="Dr. Test", specialty="General")
    db_session.add(doc)
    db_session.flush()
    for day in range(0, 7):  # open every day of the week to keep test math simple
        db_session.add(
            models.WorkingHours(
                doctor_id=doc.id, day_of_week=day, start_time=time(9, 0), end_time=time(17, 0)
            )
        )
    db_session.commit()
    db_session.refresh(doc)
    return doc


@pytest.fixture
def patient(db_session):
    p = models.Patient(name="Test Patient", email="test.patient@example.com")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def next_valid_slot(days_ahead=1, hour=10):
    """A datetime guaranteed to be >1hr in the future and inside 09:00-17:00."""
    target = utc_now() + timedelta(days=days_ahead)
    return target.replace(hour=hour, minute=0, second=0, microsecond=0)
