"""
Creates the database tables and reports current doctor/patient counts.
No demo data is inserted; populate doctors and patients via the API.

Run with: python -m app.seed
Safe to re-run: idempotent.
"""
from .database import Base, SessionLocal, engine
from . import models

Base.metadata.create_all(bind=engine)


def seed():
    db = SessionLocal()
    try:
        doctor_count = db.query(models.Doctor).count()
        patient_count = db.query(models.Patient).count()
        print(f"Database ready: {doctor_count} doctors, {patient_count} patients.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
