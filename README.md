# Clinic Booking System

A REST API for a small clinic (5 doctors) to let patients book, cancel, and
reschedule 30-minute appointment slots, without double-booking.

**Stack:** FastAPI (Python) · SQLAlchemy · SQLite (dev) / PostgreSQL (prod) · Docker · GitHub Actions · Render

---

## Deployment URL

`https://<your-render-service>.onrender.com` — **not yet deployed.**

The repo is deploy-ready: Dockerfile, GitHub Actions workflows (`ci.yml` and
`deploy.yml`), and a Render deploy-hook integration are in place. The remaining
steps that require your own accounts are:

1. Make the GitHub repo public (it currently exists but returned 404 when
   checked, so it is either private or the remote URL needs updating).
2. Create a Render web service connected to the repo.
3. Add a Render Postgres instance and set `DATABASE_URL` on the web service.
4. Add the Render deploy hook URL as the `RENDER_DEPLOY_HOOK_URL` secret in
   GitHub repo settings.

See "Deploying it yourself" below for the exact click-through steps.

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

The actual guarantee is a **partial unique index** on
`appointments(doctor_id, start_time) WHERE status = 'booked'`
(see `app/models.py`, `Index(..., sqlite_where=..., postgresql_where=...)`).

The application layer (`booking_service._validate_slot`) still does an
up-front `SELECT` for a conflicting booked row, purely so a normal (non-race)
request gets a fast, clean 409 without ever hitting the database's
constraint-violation path. But that `SELECT` + later `INSERT` is not
atomic — two requests can both pass the `SELECT` check for the same slot
before either commits. **The partial unique index is what actually closes
that race**: if both `INSERT`s reach the database, only one commits; the
second raises `IntegrityError`, which `create_appointment` catches and turns
into an HTTP 409. Cancelled appointments are excluded from the index (via the
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
| `POST` | `/appointments` | 201 | 400 (past/lead-time/outside hours/misaligned), 404 (doctor/patient), 409 (conflict) |
| `GET` | `/doctors/{id}/availability?date=YYYY-MM-DD` | 200 | 404 (doctor) |
| `PATCH` | `/appointments/{id}/cancel` | 200 | 404 (appointment), 409 (already cancelled) |
| `PATCH` | `/appointments/{id}/reschedule` | 200 | 400 (already cancelled / invalid new slot), 404, 409 (new slot conflict) |
| `GET` | `/patients/{id}/appointments` | 200 | 404 (patient) |
| `GET` | `/doctors` | 200 | — |
| `GET` | `/patients` | 200 | — |
| `GET` | `/health` | 200 | — |

`GET /doctors` and `GET /patients` aren't in the brief's required list but
exist so the API is explorable without touching the DB directly (e.g. to get
valid `doctor_id`/`patient_id` values for the demo).

### Key trade-offs

1. **SQLite for dev/tests, Postgres for CI/prod.** SQLite needs zero setup
   (no Docker, no service container) so a reviewer can `pip install` and run
   the suite in seconds. But SQLite's concurrency model is different enough
   from Postgres's that a dev-only SQLite test suite could hide the exact
   race condition this system is supposed to prevent. So CI runs the same
   suite against a real Postgres service container (see `ci.yml`), and
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

4. **Timezones: everything is naive UTC.** `start_time`/`end_time` are naive
   `datetime`s, and the API expects/returns them as such (ISO 8601 without an
   offset, uniformly UTC). For a real single-clinic deployment this needs a
   `timezone` field on `Doctor` (or a clinic-level setting) and
   timezone-aware datetimes throughout — I scoped that out for this
   assessment rather than build partial timezone support that's *more*
   dangerous than none (silently-wrong times are worse than obviously-naive
   ones). Flagged explicitly, not silently assumed away.

5. **Scaling beyond one small clinic.** The natural extension point is a
   `Clinic` model that `Doctor` belongs to, with all queries scoped by
   `clinic_id`. Because `Doctor`/`Patient`/`Appointment` already sit behind a
   service layer (not queried ad-hoc from routers), adding that scoping is a
   contained change to `booking_service.py` rather than a rewrite. The
   single Postgres DB and per-request session pattern here would also need
   connection pooling tuning and probably read replicas for the availability
   endpoint at real multi-clinic scale, but that's a "when you get there"
   concern, not a day-one one.

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
  seed.py                # idempotent demo data (5 doctors, 3 patients)
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
  ci.yml                 # tests on every PR + push to main
  deploy.yml             # tests, then deploy hook, on merge to main
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
python -m app.seed              # creates clinic.db, seeds 5 doctors + 3 patients
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

## CI/CD

- **`.github/workflows/ci.yml`** — runs on every PR into `main` and every
  push to `main`. Spins up a real Postgres 16 service container, creates the
  schema, and runs the full `pytest` suite against it. A failing test fails
  the PR check.
- **`.github/workflows/deploy.yml`** — runs on push to `main` only. It first
  calls `ci.yml` as a reusable workflow (so a broken `main` never deploys),
  then, only if tests pass, `curl`s Render's deploy hook URL (stored as the
  `RENDER_DEPLOY_HOOK_URL` repo secret) to trigger a new deploy of the
  latest image.

### Deploying it yourself

1. Push this repo to a new public GitHub repo.
2. On [Render](https://render.com): New → Web Service → connect the repo →
   Environment: Docker → it will pick up the `Dockerfile`.
3. Add a Render **Postgres** instance, copy its internal connection string
   into the web service's `DATABASE_URL` environment variable.
4. In the Render service settings, copy the **Deploy Hook URL**.
5. In GitHub repo Settings → Secrets and variables → Actions, add
   `RENDER_DEPLOY_HOOK_URL` with that value.
6. Push to `main` — `deploy.yml` will test, then trigger the Render deploy.
7. Update the "Deployment URL" section at the top of this README with the
   live `.onrender.com` URL.

Render was chosen over Fly.io mainly for this assessment because its deploy
hooks make the "CI tests pass → then deploy" flow a one-line `curl`, with no
CLI/API token plumbing needed in the workflow — a good fit for a take-home
where minimizing deploy-pipeline surface area matters more than the extra
control Fly.io's `flyctl` gives you.

---

## 4. AI-usage reflection

*(Drafted honestly from this session; not fabricated. Please review and edit before submitting — this is the AI's own account of the AI's own use.)*

**1. What the AI was used for across all sections:**
- **Section 1:** I (the AI) drafted the design document shown above — models,
  the on-the-fly slot-generation algorithm, the explicit concurrency
  mechanism (partial unique index + `IntegrityError` → 409), the API
  surface, trade-offs, and ambiguity resolutions — and paused for your
  confirmation before touching code.
- **Section 2:** I audited the existing FastAPI implementation against the
  brief, confirmed the routers/services/models split and the booking logic
  met the requirements, and then fixed a code-quality issue by introducing
  a centralized `utc_now()` helper to remove Python 3.12+ deprecation
  warnings from `datetime.utcnow()`.
- **Section 3:** I reviewed the Dockerfile, `docker-compose.yml`, and the
  two GitHub Actions workflows (`ci.yml` for PR/push tests against Postgres,
  `deploy.yml` for merge-to-main deploy hook to Render).
- **Section 4:** I drafted this reflection.

**2. One concrete example where the AI's suggestion improved the work:**
During the audit I noticed the codebase used `datetime.utcnow()` in several
places, which now raises deprecation warnings in Python 3.12+. Rather than
patch each call site with the verbose `datetime.now(timezone.utc).replace(tzinfo=None)`,
I added a single `utc_now()` helper in `app/utils.py` and used it in the
models, service, routers, and tests. This keeps the existing naive-UTC
convention intact while removing all 91 warnings from the test run, making
CI output cleaner and the code slightly more future-proof.

**3. One concrete example where the AI's output was wrong or incomplete, and
how it was caught:**
I initially assumed the workspace was empty and that I would be writing the
implementation from scratch. On listing the directory I found an existing
complete implementation, so my first plan was wrong. I corrected course by
auditing the existing code against the brief instead, ran the test suite
(12/12 passing), and focused on gaps (deprecation warnings, README accuracy,
and deployment/public-repo status) rather than duplicating work.

**4. Two decisions you made without relying on the AI:**
*(Placeholder for your own answers — the brief asks for two independent
judgment calls. Examples: whether to keep the existing single-commit history
or rewrite it, whether Render or Fly.io better fits your needs, any design
trade-off you would resolve differently, or how you plan to populate the
`RENDER_DEPLOY_HOOK_URL` secret and deploy. Please replace this paragraph
before submitting.)*

---

## Notes on what's actually in the repo vs. what still needs doing

The repo contains a complete, tested, runnable codebase — not a mockup. The
current local `main` branch has one large initial commit plus the incremental
fixes made during this audit. To satisfy the brief's "sensible commit history —
not one giant commit" requirement, you have two options:

1. **Keep the history as-is and make only granular commits going forward.**
   This is the safest option because the existing commit is already on
   `origin/main`.
2. **Rewrite history before the repo is public.** Run the suggested replay
   commands below to split the initial commit into focused commits, then
   `git push --force-with-lease` to `origin/main` (only safe if no one else
   has cloned the repo).

What still requires your own accounts:

- A public GitHub repo (the remote currently returns 404, so it is either
  private or the URL needs correcting).
- An actual deployed, publicly reachable URL on Render.
- A real `RENDER_DEPLOY_HOOK_URL` secret in the repo settings.

### Suggested commit replay (if you want to rewrite history before going public)

```bash
# Save current work as a single patch
git diff HEAD > /tmp/clinic-booking.patch

# Reset to before the first commit (DANGER: rewrites public history)
git checkout --orphan fresh-main
git rm -rf .

# Re-apply files in logical commits
git add app/database.py app/models.py .gitignore
git commit -m "feat: appointment model + db setup"

git add app/schemas.py app/services/booking_service.py app/utils.py
git commit -m "feat: booking service with slot validation + conflict handling"

git add app/routers app/main.py
git commit -m "feat: appointment, doctor, patient endpoints"

git add app/seed.py
git commit -m "feat: seed script for demo data"

git add tests/
git commit -m "test: booking, cancellation, and reschedule logic"

git add Dockerfile docker-compose.yml requirements.txt .env.example
git commit -m "chore: containerize app for local + prod parity"

git add .github/workflows/ci.yml
git commit -m "ci: run tests against postgres on every PR"

git add .github/workflows/deploy.yml
git commit -m "ci: deploy to render on merge to main"

git add README.md
git commit -m "docs: design doc, run instructions, AI reflection"

# Replace the current main branch
git branch -M main
git push --force-with-lease origin main
```

If you do **not** want to rewrite history, the commands below will commit the
current audit changes granularly on top of the existing history.
