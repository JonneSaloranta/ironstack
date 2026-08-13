# IronStack

Self-hosted, mobile-first fitness and activity tracker. See `docs/` for the
full product/architecture/domain specification and `CLAUDE.md` for the
project's development guidelines.

## Status

Phase 1 (Foundation) — Django project scaffold, custom user model,
authentication, mobile-first base layout, Docker Compose stack.

Phase 2 (Exercises) — exercise library (`apps/exercises`): system-seeded
muscle groups, equipment, and a starter exercise set; user-created custom
exercises (private to their owner); browse/search/filter UI with HTMX-backed
live filtering; soft-delete via an `active` flag so history stays intact
after an exercise is retired.

Phase 3 (Programs) — programs (`apps/programs`): `Program` → `Workout` →
`ExercisePrescription`, private to their owner except built-in system
templates (seeded, read-only, copyable via `services.copy_program`, which
deep-copies workouts/prescriptions into a new program the user can edit
independently of the source). Optional per-workout weekday scheduling.
`Program.version`/`updated_at` are display-only counters, bumped on any
structural edit — see `docs/ARCHITECTURE.md` "snapshot-on-start" for why
this is enough to satisfy the historical-trustworthiness rule once workout
logging (Phase 4) exists.

Phase 4 (Workout logging) — workout sessions (`apps/workouts`):
`WorkoutSession` → `PerformedExercise` → `ExerciseSet`. Starting a session
from a `Workout` is where snapshot-on-start actually happens:
`services.start_session` copies each prescription's exercise, sets, rep
range, target weight, and progression method onto the session's own
`PerformedExercise` rows, so later edits or deletes of the prescription
never change what the session already recorded (covered directly by
tests). Sessions can also start freeform (no program) and exercises can be
added mid-session beyond what was planned. Set logging is HTMX-driven —
each submit swaps in the updated set list and a fresh entry form
pre-filled by repeating the last set's weight/reps, so back-to-back sets
need no retyping; falls back to a normal page redirect without
JavaScript. Sessions, performed exercises, and sets are strictly private —
never shared like programs' system templates. Workout history lists
in-progress, completed, and abandoned sessions alike.

Phase 5 (PR engine) — a new `apps/records` app (not in the original
suggested list; see `docs/ARCHITECTURE.md` for why). `PersonalRecord` is
an append-only achievement log covering all six PR types from
`docs/PR_SYSTEM.md` (max weight, rep PR, rep-specific PR/"NRM", estimated
1RM, set volume, session volume). Detection (`services.check_and_record_prs`)
runs once per newly logged set, always comparing against live-computed
history — nothing is cached from a prior run, and the module never
references `Program`/`Workout`/`ExercisePrescription` at all, so program
edits provably can't touch PRs. Warmup and failed sets never count.
Estimated 1RM goes through a swappable `OneRepMaxCalculator`
(`one_rep_max.py`, Epley by default). Session volume is the one type
that's a running total rather than a single set's raw number, so later
sets in the same session update the existing record instead of each
firing their own notification — otherwise a good session would spam a
"New PR" banner after every set. Set logging shows a PR banner
(HTMX-instant, plus a Django message as a no-JS fallback); each exercise
also has a "Your PRs" page showing current bests computed live. 28 new
tests (92 total).

## Local development

```bash
cp .env.example .env   # then edit secrets
docker compose up --build
```

This starts PostgreSQL, the Django dev server (`runserver`, auto-reload) at
http://localhost:8000, and nginx in front of it on http://localhost.

Run tests:

```bash
docker compose exec web pytest
```

Create an admin user:

```bash
docker compose exec web python manage.py createsuperuser
```

### Without Docker

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env  # set POSTGRES_HOST=localhost
python manage.py migrate
python manage.py runserver
```

## Production

`docker-compose.override.yml` is a **local development** file that Docker
Compose merges in automatically. Do not ship it to a server. On the
production host, only `docker-compose.yml` should be present, and `.env`
must set real secrets, `DJANGO_ALLOWED_HOSTS`, etc. — see
`docs/SECURITY.md`.

```bash
docker compose -f docker-compose.yml up -d --build
```

## Tests & linting

```bash
ruff check .
pytest
```
