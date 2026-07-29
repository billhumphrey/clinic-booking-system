from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _naive_utc(dt: datetime) -> datetime:
    """Accept aware datetimes but store/validate as naive UTC."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    specialty: Optional[str] = None


class DoctorCreate(BaseModel):
    name: str
    specialty: Optional[str] = None


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str


class PatientCreate(BaseModel):
    name: str
    email: str


class AppointmentCreate(BaseModel):
    doctor_id: int
    patient_id: int
    start_time: datetime = Field(..., json_schema_extra={"example": "2026-07-29T10:00"})

    @field_validator("start_time")
    @classmethod
    def _normalize_start_time(cls, v: datetime) -> datetime:
        return _naive_utc(v)


class RescheduleRequest(BaseModel):
    new_start_time: datetime

    @field_validator("new_start_time")
    @classmethod
    def _normalize_new_start_time(cls, v: datetime) -> datetime:
        return _naive_utc(v)


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


class BlockedSlotCreate(BaseModel):
    start_time: datetime
    reason: Optional[str] = None

    @field_validator("start_time")
    @classmethod
    def _normalize_start_time(cls, v: datetime) -> datetime:
        return _naive_utc(v)


class BlockedSlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doctor_id: int
    start_time: datetime
    end_time: datetime
    reason: Optional[str] = None
