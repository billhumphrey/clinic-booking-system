from datetime import date, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..schemas import (
    AppointmentOut,
    BlockedSlotCreate,
    BlockedSlotOut,
    DoctorCreate,
    DoctorOut,
    SlotOut,
    WorkingHoursCreate,
    WorkingHoursOut,
)
from ..services import booking_service
from ..utils import utc_now

router = APIRouter(prefix="/doctors", tags=["doctors"])


@router.post("", response_model=DoctorOut, status_code=status.HTTP_201_CREATED)
def create_doctor(payload: DoctorCreate, db: Session = Depends(get_db)):
    return booking_service.create_doctor(db, payload.name, payload.specialty)


@router.get("", response_model=list[DoctorOut])
def list_doctors(db: Session = Depends(get_db)):
    return db.query(models.Doctor).all()


@router.post(
    "/{doctor_id}/blocked-slots",
    response_model=BlockedSlotOut,
    status_code=status.HTTP_201_CREATED,
)
def create_blocked_slot(
    doctor_id: int,
    payload: BlockedSlotCreate,
    db: Session = Depends(get_db),
):
    return booking_service.block_slot(
        db, doctor_id, payload.start_time, payload.reason
    )


@router.get("/{doctor_id}/blocked-slots", response_model=list[BlockedSlotOut])
def list_blocked_slots(
    doctor_id: int, date: date, db: Session = Depends(get_db)
):
    return booking_service.list_blocked_slots(db, doctor_id, date)


@router.delete(
    "/{doctor_id}/blocked-slots", status_code=status.HTTP_204_NO_CONTENT
)
def delete_blocked_slot(
    doctor_id: int,
    start_time: datetime,
    db: Session = Depends(get_db),
):
    booking_service.unblock_slot(db, doctor_id, start_time)

@router.get("/{doctor_id}/appointments", response_model=list[AppointmentOut])
def get_doctor_appointments(doctor_id: int, db: Session = Depends(get_db)):
    booking_service.get_doctor_or_404(db, doctor_id)
    return (
        db.query(models.Appointment)
        .filter(
            models.Appointment.doctor_id == doctor_id,
            models.Appointment.status == models.AppointmentStatus.booked,
            models.Appointment.start_time >= utc_now(),
        )
        .order_by(models.Appointment.start_time.asc())
        .all()
    )


@router.get("/{doctor_id}/availability", response_model=list[SlotOut])
def get_availability(doctor_id: int, date: date, db: Session = Depends(get_db)):
    return booking_service.get_available_slots(db, doctor_id, date)


@router.get("/{doctor_id}/working-hours", response_model=list[WorkingHoursOut])
def list_working_hours(doctor_id: int, db: Session = Depends(get_db)):
    return booking_service.get_working_hours(db, doctor_id)


@router.post(
    "/{doctor_id}/working-hours",
    response_model=list[WorkingHoursOut],
    status_code=status.HTTP_201_CREATED,
)
def set_working_hours(
    doctor_id: int,
    payload: list[WorkingHoursCreate],
    db: Session = Depends(get_db),
):
    """Bulk-set the doctor's weekly availability. Replaces any existing schedule."""
    return booking_service.set_working_hours(db, doctor_id, payload)
