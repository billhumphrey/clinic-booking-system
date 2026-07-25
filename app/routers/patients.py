from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..schemas import AppointmentOut, PatientOut
from ..services import booking_service
from ..utils import utc_now

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("", response_model=list[PatientOut])
def list_patients(db: Session = Depends(get_db)):
    return db.query(models.Patient).all()


@router.get("/{patient_id}/appointments", response_model=list[AppointmentOut])
def get_patient_appointments(patient_id: int, db: Session = Depends(get_db)):
    booking_service.get_patient_or_404(db, patient_id)
    return (
        db.query(models.Appointment)
        .filter(
            models.Appointment.patient_id == patient_id,
            models.Appointment.status == models.AppointmentStatus.booked,
            models.Appointment.start_time >= utc_now(),
        )
        .order_by(models.Appointment.start_time.asc())
        .all()
    )
