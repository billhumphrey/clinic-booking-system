from fastapi import FastAPI

from .database import Base, engine
from .routers import appointments, doctors, patients

# Using create_all() rather than Alembic migrations for this assessment's
# scope (single small table set, no schema history to manage yet). See
# README trade-offs for when this should switch to real migrations.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Clinic Booking API", version="1.0.0")

app.include_router(doctors.router)
app.include_router(patients.router)
app.include_router(appointments.router)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
