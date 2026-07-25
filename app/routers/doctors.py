from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..schemas import DoctorOut, SlotOut
from ..services import booking_service

router = APIRouter(prefix="/doctors", tags=["doctors"])


@router.get("", response_model=list[DoctorOut])
def list_doctors(db: Session = Depends(get_db)):
    return db.query(models.Doctor).all()


@router.get("/{doctor_id}/availability", response_model=list[SlotOut])
def get_availability(doctor_id: int, date: date, db: Session = Depends(get_db)):
    return booking_service.get_available_slots(db, doctor_id, date)
