# Clinic Booking System

A REST API for a small clinic (5 doctors) to let patients book, cancel, and
reschedule 30-minute appointment slots, without double-booking.

**Stack:** FastAPI (Python) · SQLAlchemy · SQLite (dev) / PostgreSQL (prod) · Docker · GitHub Actions · Render

---

## Deployment

- **Repository:** `https://github.com/billhumphrey/clinic-booking-system`
- **Live application:** `https://clinic-booking-system-1gdd.onrender.com/docs#/`
- **Database:** Render-managed PostgreSQL (`clinic-booking-db`)

The repo is deployed via Render with automated deploys from GitHub Actions. The
original setup steps are preserved below for reference:

---

## 1. System Design

### Domain models

**Doctor**
- `id`, `name`, `specialty` (nullable — not every clinic role needs one)
- Has many `WorkingHours`, has many `Appointment`

**WorkingHours** — one row per recurring availability block
- `id`, `doctor_id` (FK), `day_of_week` (0=Mon..6=Sun), `start_time`, `end_time`
- A doctor can have multiple rows per day (e.g. 09:00–12:00 and 13:00–17:00,
  with the lunch gap simply being the space between two rows). Recurring by
  day-of-week rather than by calendar date — see trade-offs below.

**Patient**
- `id`, `name`, `email` (unique)

**Appointment**
- `id`, `doctor_id` (FK), `patient_id` (FK), `start_time`, `end_time`,
  `status` (`booked` | `cancelled`), `cancellation_reason` (nullable),
  `created_at`, `updated_at`
- A cancelled row is kept (not deleted) as an audit trail; a new booking for
  the same slot is a *new* row. This is why `cancellation_reason` is nullable
  — only cancelled rows ever populate it.

### Slot generation approach

Slots are **computed on-the-fly**, not materialized into a table:

1. Look up the doctor's `WorkingHours` rows for the requested day-of-week.
2. Walk each block in 30-minute increments.
3. Subtract any `start_time` that already has a `booked` appointment that day.
4. Drop anything within 1 hour of now (the lead-time rule).

For 5 doctors this search space is trivial (a handful of blocks minus a
handful of bookings per day), so there's no material performance reason to
materialize slots. The bigger reason is **correctness under change**: a
materialized `slots` table has to be kept in sync every time working hours
change, an appointment is booked, cancelled, or rescheduled — that's several
places a stale-slot bug can creep in. Computing on read means the only source
of truth is `WorkingHours` + `Appointment`, and it's always correct by
construction. If this needed to scale to, say, hundreds of doctors and
heavy read traffic on the availability endpoint, I'd revisit this and likely
introduce a cached/materialized view refreshed on write, rather than
recomputing per request.

### Concurrency / double-booking prevention

This is the part of the brief I took most literally: "be explicit about the
mechanism, not just 'we validate it.'"

The actual guarantee is a pair of **partial unique indexes** on
`appointments(doctor_id, start_time) WHERE status = 'booked'` and
`appointments(patient_id, start_time) WHERE status = 'booked'`
(see `app/models.py`, `Index(..., sqlite_where=..., postgresql_where=...)`).

The application layer (`booking_service._validate_slot`) still does an
up-front `SELECT` for a conflicting booked row, purely so a normal (non-race)
request gets a fast, clean 409 without ever hitting the database's
constraint-violation path. But that `SELECT` + later `INSERT` is not
atomic — two requests can both pass the `SELECT` check for the same slot
before either commits. **The partial unique indexes are what actually close
that race**: if both `INSERT`s reach the database, only one commits; the
second raises `IntegrityError`, which `create_appointment` catches and turns
into an HTTP 409. Cancelled appointments are excluded from the indexes (via the
`WHERE` clause) so a freed slot can be rebooked without any special-casing.

I did *not* reach for `SELECT ... FOR UPDATE` row locking or
`SERIALIZABLE` isolation, because the unique index gives the same
correctness guarantee with far less contention and no risk of deadlocks
between concurrent transactions — it's the standard "let the database's
constraint system be the source of truth for uniqueness" pattern, and it
works identically on SQLite (dev) and Postgres (prod).

### API surface

| Method | Path | Success | Errors |
|---|---|---|---|
| `GET` | `/appointments?status=booked\|cancelled` | 200 | 422 (invalid status) |
| `POST` | `/appointments` | 201 | 400 (past/lead-time/outside hours/misaligned), 404 (doctor/patient), 409 (conflict/blocked) |
| `POST` | `/doctors` | 201 | 400 (bad input) |
| `POST` | `/patients` | 201 | 400 (bad input), 409 (duplicate email) |
| `POST` | `/doctors/{id}/blocked-slots` | 201 | 400 (past/lead-time/outside hours/misaligned), 404 (doctor), 409 (already blocked) |
| `GET` | `/doctors/{id}/blocked-slots?date=YYYY-MM-DD` | 200 | 404 (doctor) |
| `DELETE` | `/doctors/{id}/blocked-slots?start_time=YYYY-MM-DDTHH:MM:SS` | 204 | 404 (doctor/slot) |
| `GET` | `/doctors/{id}/availability?date=YYYY-MM-DD` | 200 | 404 (doctor) |
| `GET` | `/doctors/{id}/appointments` | 200 | 404 (doctor) — lists booked, future appointments only |
| `PATCH` | `/appointments/{id}/cancel` | 200 | 404 (appointment), 409 (already cancelled) |
| `PATCH` | `/appointments/{id}/reschedule` | 200 | 400 (already cancelled / invalid new slot), 404, 409 (new slot conflict/blocked) |
| `GET` | `/patients/{id}/appointments` | 200 | 404 (patient) — lists booked, future appointments only |
| `GET` | `/doctors` | 200 | — |
| `GET` | `/patients` | 200 | — |
| `GET` | `/health` | 200 | — |


### Key trade-offs

1. **SQLite for dev/tests, Postgres for CI/prod.** SQLite needs zero setup
   (no Docker, no service container) so a reviewer can `pip install` and run
   the suite in seconds. But SQLite's concurrency model is different enough
   from Postgres's that a dev-only SQLite test suite could hide the exact
   race condition this system is supposed to prevent. So CI runs the same
   suite against a real Postgres service container (see `tests.yml`), and
   production always uses Postgres. Local `docker-compose.yml` also spins up
   Postgres if you want dev to match prod exactly.

2. **Recurring weekly working hours, not calendar-specific overrides.**
   `WorkingHours` is keyed by day-of-week, not by date. This is simpler and
   covers "small clinic" reality (regular weekly hours) but can't represent
   one-off closures or holiday hours without an extra `ScheduleException`
   table. I scoped that out — flagged as an ambiguity below — but the model
   is additive-extensible (a new table, no changes to existing ones) if
   needed later.

3. **`create_all()` instead of Alembic migrations.** For a from-scratch
   schema with no migration history to manage yet, a full migrations
   framework is overhead the brief doesn't need on day one. The trade-off:
   this doesn't scale to a live production system where you need to alter an
   existing table without downtime or data loss. If this clinic system grew
   past the initial deploy, introducing Alembic would be one of the first
   things I'd add — and I've said so explicitly rather than silently
   picking the simpler option and hoping it doesn't matter.

4. **Timezones: Clinic timezone at the edge, UTC internally.** Naive
   `start_time`/`end_time` values are interpreted as clinic-local time
   (default `Africa/Nairobi`, configurable via `CLINIC_TIMEZONE`) and stored
   as naive UTC. Offset-aware ISO 8601 inputs are also accepted and converted
   to UTC. Responses convert the stored UTC value back to the clinic-local
   naive convention, so the same value can be sent back to book or reschedule.
   This fixes the production risk of the server comparing a local time against
   UTC `now()`. A full `TIMESTAMPTZ` switch is the natural next step once the
   system moves to a Postgres-only deployment and wants offset-aware values
   end-to-end.

5. **Scaling beyond one small clinic.** The natural extension point is a
   `Clinic` model that `Doctor` belongs to, with all queries scoped by
   `clinic_id`. Because `Doctor`/`Patient`/`Appointment` already sit behind a
   service layer (not queried ad-hoc from routers), adding that scoping is a
   contained change to `booking_service.py` rather than a rewrite. The
   single Postgres DB and per-request session pattern here would also need
   connection pooling tuning and probably read replicas for the availability
   endpoint at real multi-clinic scale, but that's a "when you get there"
   concern, not a day-one one.

6. **Working hours are global, not per-doctor (v1 simplification).** The
   `WorkingHours` table exists and is designed to be per-doctor, but the API
   currently uses a single shared default (09:00–17:00 clinic-local time, every
   day) for all doctors. `DoctorCreate` accepts only `name` and `specialty`,
   so there is no way to configure individual hours yet. The error message
   "outside the doctor's working hours" is therefore slightly over-promising;
   per-doctor overrides and weekend/holiday handling are the next logical
   extension.

### Ambiguities resolved (per the assessment's own instruction to flag these)

- **Cancelled appointment's `cancellation_reason`:** required on cancel
  (the brief says "cancel with a reason"), nullable in the schema since only
  cancelled rows ever have one, and left untouched by reschedule (a
  reschedule isn't a cancellation, so no reason is collected or needed).
- **Does the 1-hour-before-now rule apply to reschedule?** Yes — a
  reschedule is validated "exactly like a fresh booking" per the brief, and
  the 1-hour rule is part of that validation. So you can't reschedule an
  existing appointment to a slot inside the next hour, but I did *not*
  apply the rule to *cancelling* an appointment that starts within the next
  hour — the brief only mentions the lead-time rule for bookings.
- **What does "working hours" look like?** A recurring day-of-week +
  start/end time pair (see trade-off #2 above), not calendar-date-specific.
- **Multi-clinic vs single-clinic scope:** single-clinic for this
  assessment, with the extension path noted in trade-off #5.
- **Cancel-conflict status code:** the brief allows either 400 or 409 for
  "already cancelled" but says "pick one and be consistent" — I used **409**
  (Conflict) throughout, since "this resource is in a state that conflicts
  with the requested operation" is exactly what 409 means, and it matches
  the double-booking 409 for consistency.
- **Slot alignment:** bookings must start exactly on a 30-minute boundary
  matching the doctor's working-hour blocks (e.g. 09:00, 09:30 — not 09:15).
  This wasn't explicit in the brief; I inferred it from "30-minute
  appointment slots" being the atomic unit the whole system is built around.

---

## Project structure

```
app/
  main.py                 # FastAPI app, router registration
  database.py            # engine/session setup (SQLite/Postgres via DATABASE_URL)
  models.py              # SQLAlchemy models + the partial unique index
  schemas.py             # Pydantic request/response schemas
  seed.py                # creates tables; no demo data is inserted
  utils.py               # small helpers (e.g. naive-UTC current time)
  routers/
    doctors.py           # GET /doctors, GET /doctors/{id}/availability
    patients.py          # GET /patients, GET /patients/{id}/appointments
    appointments.py      # POST/PATCH appointment endpoints
  services/
    booking_service.py   # all business logic & validation lives here
tests/
  conftest.py            # fixtures, isolated test DB per test
  test_booking.py        # booking/cancel/reschedule/availability tests
Dockerfile
docker-compose.yml       # local dev with real Postgres
requirements.txt
.github/workflows/
  tests.yml              # tests on every PR into main/develop
  deploy.yml             # tests, then deploy hook, on push to main
```

Routers only handle HTTP concerns (parsing, status codes); all business
rules (working-hours checks, lead time, conflict detection) live in
`booking_service.py` so they're testable without spinning up HTTP at all if
needed, and so there's exactly one place double-booking logic can go wrong.

---

## Running it locally

### Option A — plain Python + SQLite (fastest)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m app.seed              # creates clinic.db (no demo data)
uvicorn app.main:app --reload   # http://localhost:8000/docs
```

### Option B — Docker Compose + Postgres (matches production)

```bash
docker-compose up --build       # http://localhost:8000/docs
```

### Running tests

```bash
pytest -v
```
Tests use SQLite by default (fast, no setup) or Postgres if `DATABASE_URL` is
set, matching what CI does.

---

## Testing the API step-by-step

The easiest way to explore the endpoints is via the interactive docs at
`https://clinic-booking-system-1gdd.onrender.com/docs#/`. Below is the same
flow using `curl`.

All times are **naive clinic-local time** (ISO 8601 without a timezone offset,
default timezone `Africa/Nairobi`, configurable via `CLINIC_TIMEZONE`). Naive
values sent in requests are interpreted as clinic-local and stored as UTC;
responses convert the stored UTC value back to clinic-local. Offset-aware inputs
are also accepted. The clinic defaults to working hours **09:00–17:00
clinic-local time, every day**. Bookings must start on a 30-minute boundary and
be made **at least 1 hour in advance**.

### 1. Create a doctor

**`POST /doctors`**

```bash
curl -X POST https://clinic-booking-system-1gdd.onrender.com/doctors \
  -H "Content-Type: application/json" \
  -d '{"name": "Dr. Ann", "specialty": "Cardiology"}'
```

Response:

```json
{"id": 1, "name": "Dr. Ann", "specialty": "Cardiology"}
```

### 2. List or create a patient

**`GET /patients`** — see existing patients:

```bash
curl https://clinic-booking-system-1gdd.onrender.com/patients
```

If the list is empty, add one with **`POST /patients`**:

```bash
curl -X POST https://clinic-booking-system-1gdd.onrender.com/patients \
  -H "Content-Type: application/json" \
  -d '{"name": "Bill Otieno", "email": "bill@example.com"}'
```

Response:

```json
{"id": 1, "name": "Bill Otieno", "email": "bill@example.com"}
```

### 3. Check the doctor’s availability

**`GET /doctors/{doctor_id}/availability?date=YYYY-MM-DD`**

Replace `1` with the doctor `id` returned in step 1, and pick a future date:

```bash
curl "https://clinic-booking-system-1gdd.onrender.com/doctors/1/availability?date=2026-08-03"
```

Response (a list of 30-minute slots still free):

```json
[
  {"start_time": "2026-08-03T09:00:00", "end_time": "2026-08-03T09:30:00"},
  {"start_time": "2026-08-03T09:30:00", "end_time": "2026-08-03T10:00:00"}
]
```

### 4. Book an appointment

**`POST /appointments`**

Pick a `start_time` from the availability response:

```bash
curl -X POST https://clinic-booking-system-1gdd.onrender.com/appointments \
  -H "Content-Type: application/json" \
  -d '{
    "doctor_id": 1,
    "patient_id": 1,
    "start_time": "2026-08-03T09:00:00"
  }'
```

Response:

```json
{
  "id": 1,
  "doctor_id": 1,
  "patient_id": 1,
  "start_time": "2026-08-03T09:00:00",
  "end_time": "2026-08-03T09:30:00",
  "status": "booked",
  "cancellation_reason": null
}
```

### 5. Cancel an appointment

**`PATCH /appointments/{appointment_id}/cancel`**

```bash
curl -X PATCH https://clinic-booking-system-1gdd.onrender.com/appointments/1/cancel \
  -H "Content-Type: application/json" \
  -d '{"reason": "Patient requested cancellation"}'
```

Response:

```json
{
  "id": 1,
  "doctor_id": 1,
  "patient_id": 1,
  "start_time": "2026-08-03T09:00:00",
  "end_time": "2026-08-03T09:30:00",
  "status": "cancelled",
  "cancellation_reason": "Patient requested cancellation"
}
```

### 6. Reschedule an appointment

**`PATCH /appointments/{appointment_id}/reschedule`**

Rescheduling requires the appointment to still be `booked`. First check
availability to pick a new slot, then:

```bash
curl -X PATCH https://clinic-booking-system-1gdd.onrender.com/appointments/1/reschedule \
  -H "Content-Type: application/json" \
  -d '{"new_start_time": "2026-08-03T10:00:00"}'
```

Response:

```json
{
  "id": 1,
  "doctor_id": 1,
  "patient_id": 1,
  "start_time": "2026-08-03T10:00:00",
  "end_time": "2026-08-03T10:30:00",
  "status": "booked",
  "cancellation_reason": null
}
```

### 7. Block a time slot as a doctor

Doctors can mark individual 30-minute slots as unavailable without creating an
appointment.

**`POST /doctors/{doctor_id}/blocked-slots`**

```bash
curl -X POST https://clinic-booking-system-1gdd.onrender.com/doctors/1/blocked-slots \
  -H "Content-Type: application/json" \
  -d '{
    "start_time": "2026-08-03T11:00:00",
    "reason": "Administrative meeting"
  }'
```

Response:

```json
{
  "id": 1,
  "doctor_id": 1,
  "start_time": "2026-08-03T11:00:00",
  "end_time": "2026-08-03T11:30:00",
  "reason": "Administrative meeting"
}
```

### 8. View or unblock a blocked slot

**`GET /doctors/{doctor_id}/blocked-slots?date=YYYY-MM-DD`** lists blocked
slots for that day:

```bash
curl "https://clinic-booking-system-1gdd.onrender.com/doctors/1/blocked-slots?date=2026-08-03"
```

To remove the block (for example, if plans change), use
**`DELETE /doctors/{doctor_id}/blocked-slots?start_time=...`**:

```bash
curl -X DELETE "https://clinic-booking-system-1gdd.onrender.com/doctors/1/blocked-slots?start_time=2026-08-03T11:00:00"
```

A successful deletion returns `204 No Content`.

---

## CI/CD

- **Pull requests into `main` or `develop`** trigger `tests.yml`. It spins up a
  real Postgres 15 service container, installs dependencies, and runs the full
  `pytest` suite. A failing test fails the PR check.
- **Pushes to `main`** trigger `deploy.yml`. It re-runs the same test job, and
  only if tests pass it `curl`s Render's deploy hook URL (stored as the
  `RENDER_DEPLOY_HOOK` repo secret) to deploy the latest image to production.

### Deploying it yourself

1. Push this repo to a new public GitHub repo.
2. On [Render](https://render.com): New → **Blueprint** → connect the repo →
   it will read `render.yaml` and create both the web service and the
   Postgres database with `DATABASE_URL` wired automatically.
   (Alternatively: New → Web Service → Environment: Docker, then manually add
   a Render Postgres instance and set `DATABASE_URL`.)
3. In the Render web service settings, copy the **Deploy Hook URL**.
4. In GitHub repo Settings → Secrets and variables → Actions, add
   `RENDER_DEPLOY_HOOK` with that value.
5. Push to `main` — `deploy.yml` will run tests, then trigger the Render deploy.
6. Update the "Deployment URL" section at the top of this README with the
   live `.onrender.com` URL.

Render was chosen over Fly.io mainly for this assessment because its deploy
hooks make the "CI tests pass → then deploy" flow a one-line `curl`, with no
CLI/API token plumbing needed in the workflow — a good fit for a take-home
where minimizing deploy-pipeline surface area matters more than the extra
control Fly.io's `flyctl` gives you.

---

## 4. AI reflection

**1. What I used AI for across the four sections**
- **Section 1:** Minor wording and formatting touch-ups on the system design
write-up (models, key decisions, and trade-offs).
- **Section 2:** Pinpointing the cause of a couple of failing booking tests
(double-booking and reschedule-conflict cases), and wording the validation
error messages in `booking_service._validate_slot` and related endpoints, so
every failure returns a clear, client-friendly `detail` string.
- **Section 3:** AI drafted the Dockerfile, `docker-compose.yml`, GitHub Actions
workflows, and `render.yaml`.
- **Section 4:** Used AI to re-structure the README file well.

**2. One AI suggestion that improved my work — and the prompt I used**
I asked: *"The booking tests are failing on double-booking and reschedule
conflicts, and the API currently returns generic 400s. How should I make the
validation return meaningful, client-friendly error messages while still
catching race conditions?"* The AI suggested centralizing all slot validation in
`booking_service._validate_slot` with explicit `HTTPException` details for each
failure mode (past slot, lead-time, boundary, outside hours, conflict), then
wrapping the database `INSERT`/`UPDATE` in a try/except for `IntegrityError` to
handle the remaining race case. That produced clearer API responses and removed
the need for scattered validation logic across routers.

**3. One AI output that was wrong or incomplete, and how I caught it**
When I asked the AI to add conflict validation for bookings, its first draft
treated every existing appointment at the same `start_time` as a conflict,
including the appointment being rescheduled. That would have blocked a patient
from rescheduling to their own current slot and broke `test_reschedule_moves_appointment_and_frees_original_slot`.
I caught it by running the reschedule test suite, then added the
`exclude_appointment_id` parameter to `_validate_slot` so the validation skips
the appointment being moved.

**4. Two decisions I made without AI**
- **Choosing the slot-generation model (computed on read vs. materialized `slots` table):** I kept slots computed from `WorkingHours` and `Appointment` rather than pre-generating them. A materialized table is faster on read but introduces synchronization bugs whenever hours, bookings, cancellations, or reschedules change; for a five-doctor clinic the read-time computation cost is negligible, so I valued correctness over the marginal read-speed gain.
- **Setting up the project:** I planned and set up the project myself, drafting the overall system design, including the domain models, the full set of API endpoints, the database schema and setup, and the deployment approach.

---

## Submission checklist

- [x] Section 1 design documented in README.
- [x] Section 2 API implemented with required + bonus endpoints.
- [x] Section 3 Dockerfile, `render.yaml`, and CI/CD workflows in place.
- [x] Public GitHub repo made public and current commits pushed.
- [x] Render web service deployed and public URL added to README.
- [x] `RENDER_DEPLOY_HOOK` secret added to GitHub repo settings.

The commit history currently begins with one large initial commit; all later
changes are granular. If you prefer a fully granular history, rewrite it
before making the repo public (only safe before anyone else has cloned it).
