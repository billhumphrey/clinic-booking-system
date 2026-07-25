from datetime import datetime, timedelta, date as date_cls

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models

SLOT_MINUTES = 30
MIN_LEAD_TIME = timedelta(hours=1)


def _slot_end(start: datetime) -> datetime:
    return start + timedelta(minutes=SLOT_MINUTES)


def get_doctor_or_404(db: Session, doctor_id: int) -> models.Doctor:
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor


def get_patient_or_404(db: Session, patient_id: int) -> models.Patient:
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


def get_appointment_or_404(db: Session, appointment_id: int) -> models.Appointment:
    appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appt


def _working_hours_for_day(db: Session, doctor_id: int, day_of_week: int):
    return (
        db.query(models.WorkingHours)
        .filter(
            models.WorkingHours.doctor_id == doctor_id,
            models.WorkingHours.day_of_week == day_of_week,
        )
        .all()
    )


def _is_within_working_hours(db: Session, doctor_id: int, start: datetime) -> bool:
    blocks = _working_hours_for_day(db, doctor_id, start.weekday())
    slot_end_time = _slot_end(start).time()
    for block in blocks:
        if block.start_time <= start.time() and slot_end_time <= block.end_time:
            return True
    return False


def get_available_slots(db: Session, doctor_id: int, day: date_cls) -> list[dict]:
    """
    Slots are computed on-the-fly (not materialized in a table): for a small
    clinic with 5 doctors the search space per day is tiny (a handful of
    working-hour blocks minus a handful of bookings), so generating on read
    avoids having to keep a slots table in sync with working-hours edits,
    cancellations, and reschedules. See README trade-offs for when this
    would need to change.
    """
    get_doctor_or_404(db, doctor_id)
    blocks = _working_hours_for_day(db, doctor_id, day.weekday())
    if not blocks:
        return []

    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    booked_rows = (
        db.query(models.Appointment.start_time)
        .filter(
            models.Appointment.doctor_id == doctor_id,
            models.Appointment.status == models.AppointmentStatus.booked,
            models.Appointment.start_time >= day_start,
            models.Appointment.start_time < day_end,
        )
        .all()
    )
    booked_starts = {row[0] for row in booked_rows}

    now = datetime.utcnow()
    slots = []
    for block in blocks:
        cur = datetime.combine(day, block.start_time)
        block_end = datetime.combine(day, block.end_time)
        while cur + timedelta(minutes=SLOT_MINUTES) <= block_end:
            if cur not in booked_starts and (cur - now) >= MIN_LEAD_TIME:
                slots.append({"start_time": cur, "end_time": _slot_end(cur)})
            cur += timedelta(minutes=SLOT_MINUTES)
    return slots


def _validate_slot(
    db: Session,
    doctor_id: int,
    start_time: datetime,
    exclude_appointment_id: int | None = None,
):
    now = datetime.utcnow()

    if start_time < now:
        raise HTTPException(status_code=400, detail="Cannot book a slot in the past")

    if start_time - now < MIN_LEAD_TIME:
        raise HTTPException(
            status_code=400, detail="Bookings must be made at least 1 hour in advance"
        )

    if start_time.minute % SLOT_MINUTES != 0 or start_time.second != 0 or start_time.microsecond != 0:
        raise HTTPException(
            status_code=400, detail="Appointments must start on a 30-minute boundary"
        )

    if not _is_within_working_hours(db, doctor_id, start_time):
        raise HTTPException(
            status_code=400, detail="Requested slot is outside the doctor's working hours"
        )

    conflict_query = db.query(models.Appointment).filter(
        models.Appointment.doctor_id == doctor_id,
        models.Appointment.start_time == start_time,
        models.Appointment.status == models.AppointmentStatus.booked,
    )
    if exclude_appointment_id is not None:
        conflict_query = conflict_query.filter(models.Appointment.id != exclude_appointment_id)

    if conflict_query.first() is not None:
        raise HTTPException(status_code=409, detail="Slot is already booked")


def create_appointment(
    db: Session, doctor_id: int, patient_id: int, start_time: datetime
) -> models.Appointment:
    get_doctor_or_404(db, doctor_id)
    get_patient_or_404(db, patient_id)
    _validate_slot(db, doctor_id, start_time)

    appointment = models.Appointment(
        doctor_id=doctor_id,
        patient_id=patient_id,
        start_time=start_time,
        end_time=_slot_end(start_time),
        status=models.AppointmentStatus.booked,
    )
    db.add(appointment)
    try:
        db.commit()
    except IntegrityError:
        # Two requests raced past the application-level check in _validate_slot.
        # The partial unique index on (doctor_id, start_time) WHERE status='booked'
        # is what actually prevents the double booking; this except clause just
        # turns that DB-level rejection into a clean 409 for the client.
        db.rollback()
        raise HTTPException(status_code=409, detail="Slot is already booked")

    db.refresh(appointment)
    return appointment


def cancel_appointment(db: Session, appointment_id: int, reason: str) -> models.Appointment:
    appt = get_appointment_or_404(db, appointment_id)
    if appt.status == models.AppointmentStatus.cancelled:
        raise HTTPException(status_code=409, detail="Appointment is already cancelled")

    appt.status = models.AppointmentStatus.cancelled
    appt.cancellation_reason = reason
    db.commit()
    db.refresh(appt)
    return appt


def reschedule_appointment(
    db: Session, appointment_id: int, new_start_time: datetime
) -> models.Appointment:
    appt = get_appointment_or_404(db, appointment_id)
    if appt.status == models.AppointmentStatus.cancelled:
        raise HTTPException(status_code=400, detail="Cannot reschedule a cancelled appointment")

    _validate_slot(db, appt.doctor_id, new_start_time, exclude_appointment_id=appt.id)

    appt.start_time = new_start_time
    appt.end_time = _slot_end(new_start_time)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Slot is already booked")

    db.refresh(appt)
    return appt
