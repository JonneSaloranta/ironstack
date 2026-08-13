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

Phase 6 (Progression engine) — `apps/progression/engine.py`:
`calculate_progression(user, prescription)` implements all seven methods
from `docs/PROGRESSION.md` (manual, maintenance, linear, double
progression, rep range, percentage-based, RPE/RIR) as pure domain logic —
no models, no views yet (that's Phase 7, "Smart suggestions"; this phase
only had to get the decision right). Every method judges past sessions
against what was actually snapshotted for them at the time (not the live
prescription), consistent with the historical-trustworthiness rule.
Failure handling is uniform: one missed session maintains, two
consecutive missed sessions at the same weight escalates to a 10%
deload. Percentage-based tries three 1RM sources in priority order —
manually supplied, latest estimated-1RM PR (`apps/records`), or a live
estimate from the most recent set — always reporting which one it used.
26 new tests (118 total), including a determinism check (same inputs →
identical `ProgressionResult`) and per-user isolation.

Phase 7 (Smart suggestions) — `apps/progression/suggestions.py`:
`WeightSuggestionEngine` (`suggest_weight(user, prescription)`), kept as
its own module separate from `ProgressionEngine` per
`docs/SMART_SUGGESTIONS.md`'s architecture note. Composes the Phase 6
decision with the prescription's rep range and a deterministic
low/medium/high confidence (based on how many sessions of evidence
backed the decision, or which 1RM source percentage-based used — never a
black-box score). Reaches the logging UI via
`apps.workouts.views._build_set_form`: a performed exercise's first set
gets pre-filled from the suggestion, shown alongside its confidence and
plain-language reason — purely a form default the user can freely
override before submitting, same as the existing "repeat last set"
convenience it falls back to for every set after the first. Since
`apps.progression` already depends on `apps.workouts` (it reads session
history), the composition happens at the view layer rather than in
`apps.workouts.services`, to avoid a circular app dependency. 14 new
tests (132 total), including the doc's own worked "reached the top of the
rep range in the last two sessions → 82.5 kg" example, and an end-to-end
test logging a materially different weight than the one suggested to
confirm nothing blocks overriding it. Also caught and fixed a real
rendering bug during manual verification: Python's `str, Enum` mix
stringifies as `Confidence.HIGH` instead of `high` — switched both
`Confidence` and `ProgressionAction` to `enum.StrEnum`.

Phase 8 (Body tracking) — `apps/measurements`: `MeasurementType`
(system-seeded weight/body fat %/waist/chest/arm/thigh/hip/neck, plus
user-created custom types — same ownership/soft-delete pattern as
`apps.exercises.Exercise`) and `BodyMeasurement` (a time-stamped
reading). `apps.core.units` gained cm/inch conversions (circumferences
round-trip through the meters canonical unit at 0.1mm precision so no
realistic input gets rounded away); `apps.measurements.units` dispatches
by `unit_kind` plus the user's metric/imperial preference onto those, so
a value is entered and displayed in whatever unit the user actually
reads, converted to canonical storage on save. Each type gets its own
history page: a hand-rolled inline-SVG line chart (no new JS dependency —
plotted server-side via `services.build_chart_series`, keeping the
scaling/normalization math out of the template) alongside the full
editable table, which doubles as the chart's accessible data source. 27
new tests (161 total). Manual HTTP verification caught and fixed a real
display bug the test suite's exact-value assertions didn't: the chart
used raw 4-decimal-place canonical values instead of the user's converted
display units — now both the chart and table read from the same
converted values.

Phase 9 (Activities) — `apps/activities`: `ActivityType` (a starting set
of common types seeded — Running, Walking, Cycling, Swimming, Hiking,
Rowing, Yoga, Other — not an exhaustive doc-specified list like
`MeasurementType`, since `docs/DOMAIN_MODEL.md` expects users to add
their own) and `Activity` (date, optional start time, duration, optional
distance/calories, notes). `build_chart_series` was generic enough
already that this phase needed the exact same thing Phase 8 built for
measurements, so rather than duplicate it or have `apps.activities`
import from the unrelated sibling `apps.measurements`, it was promoted to
`apps.core.charts` — both apps' history pages, and the shared chart
template (`templates/core/_chart.html`), now use the one shared
implementation, model-agnostic via plain `(value, date)` tuples. Each
activity type's history page charts total duration (the one metric every
activity type has, unlike distance/calories which are optional) alongside
a summary card (count, total duration, total distance/calories) and the
full editable table — this phase's "activity analytics"; cross-activity
dashboards are Phase 10. 25 new tests (186 total) plus a chart-test
relocation to `apps.core` to match the refactor. Manual HTTP verification
again caught a real bug past the test suite: the summary card's total
distance displayed raw canonical meters labeled "km" (e.g. "18000.00 km"
for what should read "18.00 km") — `services.summarize()` correctly
totals canonical values, but the view wasn't converting the total before
handing it to the template, unlike the already-converted per-entry
values. Fixed and re-verified live, with a regression test added.

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
