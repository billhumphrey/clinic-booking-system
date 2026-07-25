import enum
from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Enum,
    Index,
    Time,
)
from sqlalchemy.orm import relationship

from .database import Base


class AppointmentStatus(str, enum.Enum):
    booked = "booked"
    cancelled = "cancelled"


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    specialty = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    working_hours = relationship(
        "WorkingHours", back_populates="doctor", cascade="all, delete-orphan"
    )
    appointments = relationship("Appointment", back_populates="doctor")


class WorkingHours(Base):
    """
    One row per (doctor, day_of_week) availability block.

    day_of_week follows Python's datetime.weekday() convention: 0=Monday ... 6=Sunday.
    A doctor can have more than one block per day (e.g. 09:00-12:00 and 13:00-17:00
    with a lunch gap), so uniqueness is NOT enforced on day_of_week alone.
    """

    __tablename__ = "working_hours"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0-6
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    doctor = relationship("Doctor", back_populates="working_hours")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    appointments = relationship("Appointment", back_populates="patient")


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(Enum(AppointmentStatus), nullable=False, default=AppointmentStatus.booked)
    cancellation_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    doctor = relationship("Doctor", back_populates="appointments")
    patient = relationship("Patient", back_populates="appointments")

    __table_args__ = (
        # This is the real double-booking guarantee, not the application-level
        # query in booking_service. Only ONE row with status='booked' may exist
        # per (doctor_id, start_time); cancelled rows are excluded via the
        # partial index so a freed slot can be rebooked without deleting history.
        Index(
            "uq_doctor_slot_when_booked",
            "doctor_id",
            "start_time",
            unique=True,
            sqlite_where=(status == "booked"),
            postgresql_where=(status == "booked"),
        ),
    )
