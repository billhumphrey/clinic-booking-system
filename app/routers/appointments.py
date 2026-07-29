from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..schemas import (
    AppointmentCreate,
    AppointmentOut,
    CancelRequest,
    RescheduleRequest,
)
from ..services import booking_service

router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.get("", response_model=list[AppointmentOut])
def list_appointments(
    status: models.AppointmentStatus | None = None, db: Session = Depends(get_db)
):
    return booking_service.list_appointments(db, status.value if status else None)


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
def book_appointment(payload: AppointmentCreate, db: Session = Depends(get_db)):
    return booking_service.create_appointment(
        db, payload.doctor_id, payload.patient_id, payload.start_time
    )


@router.patch("/{appointment_id}/cancel", response_model=AppointmentOut)
def cancel_appointment(appointment_id: int, payload: CancelRequest, db: Session = Depends(get_db)):
    return booking_service.cancel_appointment(db, appointment_id, payload.reason)


@router.patch("/{appointment_id}/reschedule", response_model=AppointmentOut)
def reschedule_appointment(
    appointment_id: int, payload: RescheduleRequest, db: Session = Depends(get_db)
):
    return booking_service.reschedule_appointment(db, appointment_id, payload.new_start_time)
