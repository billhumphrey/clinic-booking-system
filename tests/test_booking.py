from datetime import date, datetime, time, timedelta

from app import models
from app.services import booking_service
from app.utils import DAY_NAMES, parse_day_of_week, utc_now
from .conftest import next_valid_slot


def test_create_doctor(client):
    resp = client.post("/doctors", json={"name": "Dr. New", "specialty": "Neurology"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Dr. New"
    assert body["specialty"] == "Neurology"


def test_create_patient(client):
    resp = client.post(
        "/patients", json={"name": "Jane Doe", "email": "jane.doe@example.com"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Jane Doe"
    assert body["email"] == "jane.doe@example.com"


def test_block_slot_hides_from_availability(client, doctor):
    slot = next_valid_slot()
    resp = client.post(
        f"/doctors/{doctor.id}/blocked-slots",
        json={"start_time": slot.isoformat(), "reason": "Lunch break"},
    )
    assert resp.status_code == 201

    avail = client.get(
        f"/doctors/{doctor.id}/availability", params={"date": slot.date().isoformat()}
    )
    assert avail.status_code == 200
    assert slot.isoformat() not in [s["start_time"] for s in avail.json()]


def test_unblock_slot_restores_availability(client, doctor):
    slot = next_valid_slot()
    client.post(
        f"/doctors/{doctor.id}/blocked-slots",
        json={"start_time": slot.isoformat()},
    )

    del_resp = client.delete(
        f"/doctors/{doctor.id}/blocked-slots",
        params={"start_time": slot.isoformat()},
    )
    assert del_resp.status_code == 204

    avail = client.get(
        f"/doctors/{doctor.id}/availability", params={"date": slot.date().isoformat()}
    )
    assert avail.status_code == 200
    assert slot.isoformat() in [s["start_time"] for s in avail.json()]


def test_block_slot_invalid_doctor(client):
    slot = next_valid_slot()
    resp = client.post(
        "/doctors/999/blocked-slots",
        json={"start_time": slot.isoformat()},
    )
    assert resp.status_code == 404


def test_successful_booking(client, doctor, patient):
    slot = next_valid_slot()
    resp = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "booked"
    assert body["doctor_id"] == doctor.id


def test_list_appointments(client, doctor, patient):
    slot = next_valid_slot()
    client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()},
    )

    resp = client.get("/appointments")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert any(a["doctor_id"] == doctor.id and a["patient_id"] == patient.id for a in body)

    resp = client.get("/appointments", params={"status": "cancelled"})
    assert resp.status_code == 200
    assert all(a["status"] == "cancelled" for a in resp.json())


def test_double_booking_rejected(client, doctor, patient):
    slot = next_valid_slot()
    payload = {"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()}

    first = client.post("/appointments", json=payload)
    assert first.status_code == 201

    second = client.post("/appointments", json=payload)
    assert second.status_code == 409


def test_booking_outside_working_hours(client, doctor, patient):
    slot = next_valid_slot(hour=20)  # test doctor's hours are 09:00-17:00
    resp = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()},
    )
    assert resp.status_code == 400


def test_booking_in_the_past(client, doctor, patient):
    slot = next_valid_slot(days_ahead=-1)
    resp = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()},
    )
    assert resp.status_code == 400


def test_booking_within_one_hour_of_now_rejected(client, doctor, patient):
    slot = utc_now() + timedelta(minutes=30)
    resp = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()},
    )
    assert resp.status_code == 400


def test_cancel_then_rebook_same_slot(client, doctor, patient):
    slot = next_valid_slot()
    payload = {"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()}
    created = client.post("/appointments", json=payload).json()

    cancel_resp = client.patch(
        f"/appointments/{created['id']}/cancel", json={"reason": "Patient request"}
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    rebook = client.post("/appointments", json=payload)
    assert rebook.status_code == 201


def test_reschedule_conflict(client, doctor, patient):
    slot_a = next_valid_slot(hour=10)
    slot_b = next_valid_slot(hour=11)

    client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot_a.isoformat()},
    )
    appt_b = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot_b.isoformat()},
    ).json()

    resp = client.patch(
        f"/appointments/{appt_b['id']}/reschedule", json={"new_start_time": slot_a.isoformat()}
    )
    assert resp.status_code == 409


def test_cancel_already_cancelled_appointment(client, doctor, patient):
    slot = next_valid_slot()
    created = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()},
    ).json()

    first_cancel = client.patch(
        f"/appointments/{created['id']}/cancel", json={"reason": "Change of plans"}
    )
    assert first_cancel.status_code == 200

    second_cancel = client.patch(
        f"/appointments/{created['id']}/cancel", json={"reason": "Again"}
    )
    assert second_cancel.status_code == 409


def test_reschedule_moves_appointment_and_frees_original_slot(client, doctor, patient):
    slot_a = next_valid_slot(hour=10)
    slot_b = next_valid_slot(hour=14)

    created = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot_a.isoformat()},
    ).json()

    resp = client.patch(
        f"/appointments/{created['id']}/reschedule", json={"new_start_time": slot_b.isoformat()}
    )
    assert resp.status_code == 200
    assert resp.json()["start_time"].startswith(slot_b.isoformat()[:16])

    # original slot must be bookable again after the reschedule
    rebook = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot_a.isoformat()},
    )
    assert rebook.status_code == 201


def test_reschedule_cancelled_appointment_rejected(client, doctor, patient):
    slot_a = next_valid_slot(hour=10)
    slot_b = next_valid_slot(hour=14)

    created = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot_a.isoformat()},
    ).json()
    client.patch(f"/appointments/{created['id']}/cancel", json={"reason": "No longer needed"})

    resp = client.patch(
        f"/appointments/{created['id']}/reschedule", json={"new_start_time": slot_b.isoformat()}
    )
    assert resp.status_code == 400


def test_get_availability_excludes_booked_slot(client, doctor, patient):
    slot = next_valid_slot(hour=10)
    client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()},
    )

    resp = client.get(f"/doctors/{doctor.id}/availability", params={"date": slot.date().isoformat()})
    assert resp.status_code == 200
    starts = [s["start_time"] for s in resp.json()]
    assert slot.isoformat() not in starts


def test_patient_upcoming_appointments_sorted(client, doctor, patient):
    slot_a = next_valid_slot(days_ahead=2, hour=10)
    slot_b = next_valid_slot(days_ahead=1, hour=10)

    client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot_a.isoformat()},
    )
    client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot_b.isoformat()},
    )

    resp = client.get(f"/patients/{patient.id}/appointments")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 2
    assert results[0]["start_time"] < results[1]["start_time"]


def test_booking_blocked_slot_rejected(client, doctor, patient):
    slot = next_valid_slot()
    block = client.post(
        f"/doctors/{doctor.id}/blocked-slots",
        json={"start_time": slot.isoformat(), "reason": "Conference"},
    )
    assert block.status_code == 201

    resp = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()},
    )
    assert resp.status_code == 409


def test_reschedule_blocked_slot_rejected(client, doctor, patient):
    slot_a = next_valid_slot(hour=10)
    slot_b = next_valid_slot(hour=11)
    blocked = next_valid_slot(hour=14)

    appt = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot_a.isoformat()},
    ).json()

    client.post(
        f"/doctors/{doctor.id}/blocked-slots",
        json={"start_time": blocked.isoformat(), "reason": "Lunch"},
    )

    resp = client.patch(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_start_time": blocked.isoformat()},
    )
    assert resp.status_code == 409

    # sanity: the original target is still free for rescheduling
    ok = client.patch(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_start_time": slot_b.isoformat()},
    )
    assert ok.status_code == 200


def test_patient_double_booking_across_doctors_rejected(client, doctor, patient, db_session):
    second_doc = models.Doctor(name="Dr. Second", specialty="Dermatology")
    db_session.add(second_doc)
    db_session.flush()
    for day_name in DAY_NAMES:
        db_session.add(
            models.WorkingHours(
                doctor_id=second_doc.id,
                day_of_week=parse_day_of_week(day_name),
                start_time=time(9, 0),
                end_time=time(17, 0),
            )
        )
    db_session.commit()
    db_session.refresh(second_doc)

    slot = next_valid_slot()
    payload_a = {"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()}
    payload_b = {"doctor_id": second_doc.id, "patient_id": patient.id, "start_time": slot.isoformat()}

    first = client.post("/appointments", json=payload_a)
    assert first.status_code == 201

    second = client.post("/appointments", json=payload_b)
    assert second.status_code == 409


def test_invalid_status_filter_returns_422(client):
    resp = client.get("/appointments", params={"status": "bogus"})
    assert resp.status_code == 422


def test_clinic_timezone_interpretation(client, doctor, patient, monkeypatch):
    """A naive 13:00 when the clinic is in EAT should be stored as 10:00 UTC and returned as 13:00."""
    from app import utils

    monkeypatch.setattr(utils, "CLINIC_TIMEZONE", "Africa/Nairobi")
    slot = next_valid_slot(hour=13)  # 13:00 EAT == 10:00 UTC

    resp = client.post(
        "/appointments",
        json={"doctor_id": doctor.id, "patient_id": patient.id, "start_time": slot.isoformat()},
    )
    assert resp.status_code == 201
    body = resp.json()
    # Response is converted back to clinic-local time.
    assert body["start_time"] == slot.isoformat()


def test_day_boundary_uses_clinic_timezone(db_session, doctor, monkeypatch):
    """When the clinic timezone is UTC+3, a requested clinic date must map to the
    correct UTC window; bookings/blocks at the edges must not leak into adjacent days.
    """
    from app import utils

    monkeypatch.setattr(utils, "CLINIC_TIMEZONE", "Africa/Nairobi")

    patient_a = models.Patient(name="Patient A", email="patient.a@example.com")
    patient_b = models.Patient(name="Patient B", email="patient.b@example.com")
    db_session.add_all([patient_a, patient_b])
    db_session.flush()

    target_date = date(2026, 7, 29)
    late_local = datetime(2026, 7, 29, 22, 0)
    from app.utils import as_utc

    late_utc = as_utc(late_local)
    assert late_utc == datetime(2026, 7, 29, 19, 0)

    db_session.add(
        models.Appointment(
            doctor_id=doctor.id,
            patient_id=patient_a.id,
            start_time=late_utc,
            end_time=late_utc + timedelta(minutes=30),
            status=models.AppointmentStatus.booked,
        )
    )

    # Appointment at 00:00 the next clinic day -> 21:00 UTC previous day,
    # which must NOT be attributed to target_date.
    next_day_local = datetime(2026, 7, 30, 0, 0)
    next_day_utc = as_utc(next_day_local)
    assert next_day_utc == datetime(2026, 7, 29, 21, 0)

    db_session.add(
        models.Appointment(
            doctor_id=doctor.id,
            patient_id=patient_b.id,
            start_time=next_day_utc,
            end_time=next_day_utc + timedelta(minutes=30),
            status=models.AppointmentStatus.booked,
        )
    )
    db_session.commit()

    slots = booking_service.get_available_slots(db_session, doctor.id, target_date)
    slot_starts = {s["start_time"] for s in slots}
    assert late_utc not in slot_starts
    assert next_day_utc not in slot_starts

    # Block a slot at 16:00 clinic time -> 13:00 UTC, inside working hours and the day.
    blocked_local = datetime(2026, 7, 29, 16, 0)
    booking_service.block_slot(
        db_session, doctor.id, as_utc(blocked_local), reason="Meeting"
    )

    blocked = booking_service.list_blocked_slots(db_session, doctor.id, target_date)
    blocked_starts = {bs.start_time for bs in blocked}
    assert as_utc(blocked_local) in blocked_starts
