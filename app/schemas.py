from datetime import datetime, time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from app.utils import as_utc, format_day_of_week, from_utc, parse_day_of_week


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
        return as_utc(v)


class RescheduleRequest(BaseModel):
    new_start_time: datetime

    @field_validator("new_start_time")
    @classmethod
    def _normalize_new_start_time(cls, v: datetime) -> datetime:
        return as_utc(v)


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

    @field_serializer("start_time", "end_time")
    @classmethod
    def _serialize_datetime(cls, v: datetime) -> datetime:
        return from_utc(v)


class SlotOut(BaseModel):
    start_time: datetime
    end_time: datetime

    @field_serializer("start_time", "end_time")
    @classmethod
    def _serialize_datetime(cls, v: datetime) -> datetime:
        return from_utc(v)


class BlockedSlotCreate(BaseModel):
    start_time: datetime
    reason: Optional[str] = None

    @field_validator("start_time")
    @classmethod
    def _normalize_start_time(cls, v: datetime) -> datetime:
        return as_utc(v)


class BlockedSlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doctor_id: int
    start_time: datetime
    end_time: datetime
    reason: Optional[str] = None

    @field_serializer("start_time", "end_time")
    @classmethod
    def _serialize_datetime(cls, v: datetime) -> datetime:
        return from_utc(v)


class WorkingHoursCreate(BaseModel):
    day_of_week: str = Field(
        ...,
        description="Day name or abbreviation, e.g. 'Monday', 'Mon', 'Mo' (also accepts 0-6)",
    )
    start_time: time
    end_time: time

    @field_validator("day_of_week", mode="before")
    @classmethod
    def _normalize_day(cls, v):
        return format_day_of_week(parse_day_of_week(v))

    @model_validator(mode="after")
    def _start_before_end(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class WorkingHoursOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doctor_id: int
    day_of_week: str
    start_time: time
    end_time: time

    @field_validator("day_of_week", mode="before")
    @classmethod
    def _format_day(cls, v):
        if isinstance(v, int):
            return format_day_of_week(v)
        return v
