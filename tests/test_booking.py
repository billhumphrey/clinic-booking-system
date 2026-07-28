from datetime import timedelta

from app.utils import utc_now
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
