"""
Seeds the database with 5 doctors (Mon-Fri, 09:00-12:00 and 13:00-17:00)
and a few demo patients, so the deployed app is usable out of the box.

Run with: python -m app.seed
Safe to re-run: no-ops if doctors already exist.
"""
from datetime import time

from .database import Base, SessionLocal, engine
from . import models

Base.metadata.create_all(bind=engine)

DOCTORS = [
    {"name": "Dr. Amina Yusuf", "specialty": "General Practice"},
    {"name": "Dr. Brian Otieno", "specialty": "Pediatrics"},
    {"name": "Dr. Carol Njoroge", "specialty": "Dermatology"},
    {"name": "Dr. David Mwangi", "specialty": "Cardiology"},
    {"name": "Dr. Esther Wanjiru", "specialty": "Orthopedics"},
]

PATIENTS = [
    {"name": "John Kamau", "email": "john.kamau@example.com"},
    {"name": "Grace Achieng", "email": "grace.achieng@example.com"},
    {"name": "Peter Mutua", "email": "peter.mutua@example.com"},
]


def seed():
    db = SessionLocal()
    try:
        if db.query(models.Doctor).count() > 0:
            print("Database already seeded, skipping.")
            return

        for d in DOCTORS:
            doctor = models.Doctor(**d)
            db.add(doctor)
            db.flush()  # populate doctor.id before creating working_hours rows
            for day in range(0, 5):  # Mon-Fri
                db.add(
                    models.WorkingHours(
                        doctor_id=doctor.id,
                        day_of_week=day,
                        start_time=time(9, 0),
                        end_time=time(12, 0),
                    )
                )
                db.add(
                    models.WorkingHours(
                        doctor_id=doctor.id,
                        day_of_week=day,
                        start_time=time(13, 0),
                        end_time=time(17, 0),
                    )
                )

        for p in PATIENTS:
            db.add(models.Patient(**p))

        db.commit()
        print("Seed complete: 5 doctors, 3 patients.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
