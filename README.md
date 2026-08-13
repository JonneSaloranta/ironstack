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

Phase 10 (Analytics) — `apps/analytics`. `apps.measurements`/
`apps.activities` already had dedicated trend pages from Phases 8-9, so
this phase adds only what didn't have a home yet: a dashboard
(`/analytics/` — training summary, a weekly-volume bar chart, a
muscle-group-volume bar chart, PR history) and per-exercise strength
trend (`/analytics/exercises/<pk>/` — estimated 1RM over time, one point
per session). `apps.core.charts` gained `build_bar_series` alongside the
existing line-chart builder, so both chart types share one tested,
model-agnostic foundation. Date-range filtering
(`apps.analytics.dateranges`) is shared across both pages: six presets
plus an explicit `start`/`end` override for docs/ANALYTICS.md's "custom
range". Training-load volume here deliberately counts failed sets (the
work still happened) — a real, intentional divergence from
`apps.records`' stricter PR eligibility, not an inconsistency. Also
enhanced `apps.core`'s main dashboard with the widgets
`docs/UI.md` calls for: this week's volume, recent PRs, latest body
weight. 51 new tests (222 total). Manually seeding realistic workout
history to verify live surfaced a false alarm rather than a real bug —
"Training time: 32 days" from a first pass — traced to the seeding
script itself (it backdated `started_at` without also backdating
`ended_at`), not the analytics query; re-seeding with a realistic
`ended_at` showed the correct duration, confirming the calculation was
right all along. Also confirmed the estimated-1RM trend, both bar
charts, and the date-range filter each read correctly against real
multi-session history over HTTP.

Phase 11 (Polish) — final pass across the whole app rather than one app's
worth of new features:

- **Query review**: two real N+1s found and fixed. The programs list
  called `program.workouts.count` per row in the template (twice —
  once for the number, once for `|pluralize`); the activities list
  called the full `services.summarize()` per row, materializing every
  logged `Activity` row just to display a count. Both replaced with a
  single annotated `Count()` query per list — the activities fix
  specifically filters on the current user (`Count("activities",
  filter=Q(activities__user=user))`), since an unfiltered count would
  have shown every user's total against a shared system type. Added
  `db_index=True` to the four date fields every history/analytics view
  filters and orders by (`WorkoutSession.started_at`,
  `PersonalRecord.achieved_at`, `BodyMeasurement.recorded_at`,
  `Activity.date`) — targeted at query patterns already built, not
  speculative.
- **Accessibility**: a skip-to-content link, `.htmx-request` styling so
  every HTMX interaction in the app gets free loading feedback with no
  per-form wiring, and an audit confirming every hand-rolled `<input>`/
  `<select>` outside the standard `field.label_tag` form loop already
  carries an explicit `aria-label`.
- **Error handling**: custom 404/403 templates (extend `base.html` —
  Django passes them a normal request context) and a deliberately
  *standalone* 500 template with hand-copied inline styles, since Django
  renders it with no context processors at all — the moment it's needed
  may mean the DB or session backend itself is down, so it can't lean on
  `{% url %}`, `{% static %}`, or anything else `base.html` depends on.
- **Mobile**: wide tables (set logging, measurement/activity history)
  now scroll within themselves (`.table-wrap { overflow-x: auto }`)
  instead of pushing the whole page wider on narrow screens.
- **A real functional gap, not just visual polish**: the "Profile"
  bottom-nav tab had been a dead `href="#"` placeholder since Phase 1 —
  and `unit_system`/`timezone` had driven unit conversion since Phase 8
  with no UI to ever change them after signup. Built
  `apps.accounts.ProfileView` (unit system, timezone — a real
  `zoneinfo.available_timezones()` dropdown, not free text) and wired up
  Django's built-in password-change flow, whose URLs had existed since
  Phase 1 but had no templates — visiting `/accounts/password_change/`
  would have thrown `TemplateDoesNotExist`.
- **Docker production review**: `python manage.py check --deploy`
  against `config.settings.production` passes clean. Found and fixed a
  real deployment footgun: the bundled nginx config is HTTP-only, but
  `DJANGO_SECURE_SSL_REDIRECT` defaults `true` — deployed exactly as
  shipped, this is an infinite redirect loop (nginx always reports
  `X-Forwarded-Proto: http`, Django redirects every request to HTTPS,
  which routes right back through the same HTTP-only nginx). Documented
  clearly in `docs/SECURITY.md` with the three ways to actually resolve
  it, rather than silently shipping a broken default or bundling
  certificate automation that can't be generically tested without a real
  domain. Also added a `web` healthcheck (hits the existing `/healthz/`)
  and gated nginx's startup on it (`depends_on: web: condition:
  service_healthy`) instead of nginx merely waiting for the container to
  start rather than actually be ready — verified live: nginx now visibly
  waits through "Waiting → Healthy" before starting. Added explicit
  gunicorn worker/timeout/logging flags, previously left at gunicorn's
  bare defaults (1 worker).
- 10 new tests (232 total): profile updates (including an invalid-
  timezone rejection), the password-change flow end-to-end (change
  password, confirm the new one authenticates), both N+1 fixes'
  annotated counts (including the cross-user scoping correctness for the
  activities one), and the custom error templates.

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

**Before your first production deploy**, read `docs/SECURITY.md`'s "TLS"
section — the bundled nginx config is HTTP-only, and the default
`DJANGO_SECURE_SSL_REDIRECT=true` will redirect-loop until you either put
a TLS-terminating proxy in front of this stack or explicitly opt out.

```bash
docker compose -f docker-compose.yml up -d --build
```

## Tests & linting

```bash
ruff check .
pytest
```
