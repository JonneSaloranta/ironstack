# Architecture

## Stack

- Django
- PostgreSQL
- Django Templates
- HTMX
- Alpine.js
- CSS
- Docker Compose

The application is server-rendered first.

## Application boundaries

Suggested Django applications:

```text
accounts
exercises
programs
workouts
records
progression
measurements
activities
analytics
core
```

`records` (PR engine) was not in CLAUDE.md's original suggested list; it
was added in Phase 5 as the structure evolved, per CLAUDE.md's "exact
structure may evolve if implementation demonstrates a better
organization." PRs are a big enough concern (own model, own detection
logic, own future dashboard) that folding them into `workouts` would have
started overloading that app; `records` depends one-directionally on
`workouts` (`PersonalRecord.source_set` and PR detection read
`ExerciseSet` history) and on `exercises` (`PersonalRecord.exercise`) —
neither of those apps knows `records` exists, so the dependency only ever
points one way.

Keep domain logic separated from presentation.

Views should orchestrate requests and responses. Complex rules belong in domain/service modules.

## Domain boundaries

### accounts
Authentication, profiles, preferences, units.

### exercises
Exercises, muscle groups, equipment, user-created exercises.

### programs
Programs, workouts, prescriptions, scheduling, templates, program versions.

### workouts
Workout sessions, performed sets, workout history.

### records
Personal record detection and storage (max weight, rep PRs, rep-specific
PRs, estimated 1RM, set/session volume). Derived from `workouts` history
only — never from `programs` — so program edits can't affect PRs.

### progression
Progression methods and weight suggestion logic. Has no models of its
own — a decision is recomputed live from `workouts` (session/set history)
and `records` (as one of the three 1RM sources for percentage-based
progression) each time, the same "derive, don't cache" approach `records`
uses for PRs. Not yet wired into any view/template — `docs/ROADMAP.md`
Phase 7 ("Smart suggestions") is where this becomes a UI-facing weight
suggestion; Phase 6 only had to deliver the underlying decision.

### measurements
Body weight, body fat, circumferences, custom measurements.

### activities
Manually logged non-gym activities.

### analytics
Aggregations, trends, dashboards, chart data.

### core
Shared utilities and cross-cutting concerns: unit conversion
(`apps.core.units`) and single-series chart data prep
(`apps.core.charts.build_chart_series` — normalizes a list of `(value,
date)` readings into SVG-ready coordinates; kept model-agnostic so any
app plotting a trend, currently `measurements` and `activities`, shares
one implementation instead of each rolling its own).

## Historical data rule

Completed workout data represents what actually happened.

A program edit must not rewrite historical sessions.

For example:

```text
Program v1
Bench Press: 3 × 8

Program v2
Bench Press: 4 × 8
```

A workout performed under v1 must remain a 3-set workout even after v2 exists.

Store enough snapshot/prescription information on the performed workout/set records to preserve historical truth.

### Historical integrity mechanism: snapshot-on-start

The mechanism chosen for this is **snapshot-on-start**, not full program
row-versioning.

When a `WorkoutSession` is created from a `Workout`, the prescription data it
needs (exercise, set count, rep range, target weight, progression method,
increment, ordering, notes) is copied onto the session's own performed
records at creation time. `Program`, `Workout`, and `ExercisePrescription`
rows can then be edited or deactivated freely afterward — history never
looks them up for its numbers, because it holds its own copy.

`Program` keeps a simple `updated_at` timestamp and an incrementing
`version` integer purely for display ("this program was edited on ...").
This is not a row-versioning system; there is no `ProgramVersion` table that
duplicates workouts/prescriptions. This keeps the schema small while fully
satisfying the historical-trustworthiness rule, since the snapshot — not the
live program — is the source of truth for anything already performed.

## Units and precision

- All weights, one-rep-max values, and body measurements are stored as
  `DecimalField` (not `float`), to avoid rounding errors compounding through
  progression math and PR comparisons.
- Canonical storage unit is kilograms for weight and meters for distance,
  regardless of the user's display preference. Conversion to the user's
  preferred unit system happens only at the template/service boundary.
- All timestamps are stored in UTC (`USE_TZ=True`); "today"/"this week"
  boundaries in the dashboard and analytics are computed using the user's
  stored timezone preference.

## API layer

No REST/DRF API is built in the initial implementation. `ARCHITECTURE.md`'s
extensibility goal (future mobile clients, integrations) is satisfied by
keeping domain services HTTP-agnostic, which is already required. A thin API
layer can be added later without touching domain logic. Do not add Django
REST Framework or similar until an actual client needs it.

## Domain services

Important services should be independently testable.

Examples:

```text
ProgressionEngine
WeightSuggestionEngine
PRService
OneRepMaxCalculator
AnalyticsService
```

Avoid large monolithic services. Split responsibilities when they become difficult to test or understand.

`apps.records` implements `PRService` and `OneRepMaxCalculator` as plain
functions/a small class in `services.py`/`one_rep_max.py` rather than a
single `PRService` class — there was no shared state or polymorphism to
justify a class, per "keep the actual API clean and idiomatic" (see
`docs/PROGRESSION.md`, which asks the same of the future progression
service). `OneRepMaxCalculator` is a real class, though, since swapping
formulas (`docs/PR_SYSTEM.md`) is naturally an object with configuration.

## Security

Every user-owned object must be scoped to the authenticated user.

Explicitly test that one user cannot access another user's:
- programs
- workouts
- sets
- personal records
- measurements
- activities
- analytics data

## Extensibility

The architecture should allow future integrations such as health platforms or mobile applications, but these are not part of the initial implementation.
