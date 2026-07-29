from datetime import datetime, timedelta, time, date as date_cls

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..schemas import _naive_utc
from ..utils import utc_now

SLOT_MINUTES = 30
MIN_LEAD_TIME = timedelta(hours=1)

DEFAULT_START_TIME = time(9, 0)
DEFAULT_END_TIME = time(17, 0)


def _default_working_hours(day_of_week: int) -> list[tuple[time, time]]:
    """
    All doctors share the same working hours for this assessment.
    Slots are generated from this function instead of the DB so availability
    is always computed fresh and only excludes booked/blocked slots.
    """
    return [(DEFAULT_START_TIME, DEFAULT_END_TIME)]


def _slot_end(start: datetime) -> datetime:
    return start + timedelta(minutes=SLOT_MINUTES)


def create_doctor(db: Session, name: str, specialty: str | None = None) -> models.Doctor:
    doctor = models.Doctor(name=name, specialty=specialty)
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    return doctor


def get_doctor_or_404(db: Session, doctor_id: int) -> models.Doctor:
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor


def _blocked_slots_for_day(
    db: Session, doctor_id: int, day: date_cls
) -> list[models.BlockedSlot]:
    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    return (
        db.query(models.BlockedSlot)
        .filter(
            models.BlockedSlot.doctor_id == doctor_id,
            models.BlockedSlot.start_time >= day_start,
            models.BlockedSlot.start_time < day_end,
        )
        .all()
    )


def create_patient(db: Session, name: str, email: str) -> models.Patient:
    existing = db.query(models.Patient).filter(models.Patient.email == email).first()
    if existing:
        raise HTTPException(
            status_code=409, detail="Patient with this email already exists"
        )
    patient = models.Patient(name=name, email=email)
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


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


def list_appointments(
    db: Session, status_filter: str | None = None
) -> list[models.Appointment]:
    query = db.query(models.Appointment)
    if status_filter:
        query = query.filter(models.Appointment.status == status_filter)
    return query.order_by(models.Appointment.start_time.asc()).all()


def _is_within_working_hours(db: Session, doctor_id: int, start: datetime) -> bool:
    # db/doctor_id are kept for future per-doctor overrides; default is shared.
    slot_end_time = _slot_end(start).time()
    for block_start, block_end in _default_working_hours(start.weekday()):
        if block_start <= start.time() and slot_end_time <= block_end:
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
    blocked_starts = {
        bs.start_time for bs in _blocked_slots_for_day(db, doctor_id, day)
    }

    now = utc_now()
    slots = []
    for block_start, block_end in _default_working_hours(day.weekday()):
        cur = datetime.combine(day, block_start)
        block_end_dt = datetime.combine(day, block_end)
        while cur + timedelta(minutes=SLOT_MINUTES) <= block_end_dt:
            if (
                cur not in booked_starts
                and cur not in blocked_starts
                and (cur - now) >= MIN_LEAD_TIME
            ):
                slots.append({"start_time": cur, "end_time": _slot_end(cur)})
            cur += timedelta(minutes=SLOT_MINUTES)
    return slots


def block_slot(
    db: Session, doctor_id: int, start_time: datetime, reason: str | None = None
) -> models.BlockedSlot:
    get_doctor_or_404(db, doctor_id)
    _validate_slot(db, doctor_id, start_time)

    blocked = models.BlockedSlot(
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=_slot_end(start_time),
        reason=reason,
    )
    db.add(blocked)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Slot is already blocked")
    db.refresh(blocked)
    return blocked


def list_blocked_slots(
    db: Session, doctor_id: int, day: date_cls
) -> list[models.BlockedSlot]:
    get_doctor_or_404(db, doctor_id)
    return _blocked_slots_for_day(db, doctor_id, day)


def unblock_slot(db: Session, doctor_id: int, start_time: datetime) -> None:
    start_time = _naive_utc(start_time)
    blocked = (
        db.query(models.BlockedSlot)
        .filter(
            models.BlockedSlot.doctor_id == doctor_id,
            models.BlockedSlot.start_time == start_time,
        )
        .first()
    )
    if not blocked:
        raise HTTPException(status_code=404, detail="Blocked slot not found")
    db.delete(blocked)
    db.commit()


def _validate_slot(
    db: Session,
    doctor_id: int,
    start_time: datetime,
    exclude_appointment_id: int | None = None,
):
    now = utc_now()

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
