import os
from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app import models
from app.utils import utc_now

# Respect DATABASE_URL if set (CI runs this against a real Postgres service
# container to match production), otherwise fall back to a local SQLite file
# so contributors can run the suite with zero setup.
TEST_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test_clinic.db")
connect_args = {"check_same_thread": False} if TEST_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(TEST_DATABASE_URL, connect_args=connect_args)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    return TestingSessionLocal()


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
