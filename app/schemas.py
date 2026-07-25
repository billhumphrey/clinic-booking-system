from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    specialty: Optional[str] = None


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str


class AppointmentCreate(BaseModel):
    doctor_id: int
    patient_id: int
    start_time: datetime


class RescheduleRequest(BaseModel):
    new_start_time: datetime


class CancelRequest(BaseModel):
    reason: str


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doctor_id: int
    patient_id: int
    start_time: datetime
    end_time: datetime
    status: str
    cancellation_reason: Optional[str] = None


class SlotOut(BaseModel):
    start_time: datetime
    end_time: datetime
