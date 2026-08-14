# Domain Model

## User

Use Django's custom user model.

User-related data includes:
- authentication
- profile
- preferences
- preferred units

Internal values should use consistent canonical units. Convert for display.

Also carries an optional `height` (canonical meters, entered/displayed in
cm or inches like any other length reading — see `apps.core.units`) and a
`show_bmi` toggle. Both exist solely for `apps.core.bmi`: the dashboard
computes BMI from `height` and the user's latest logged body weight
(`apps.measurements`) whenever both exist, showing the WHO category
thresholds (underweight/normal/overweight/obese) alongside the current
value so the number has context rather than standing alone. Once a
height is on file, `apps.core.bmi.category_rows` also converts each
category's BMI bounds into the equivalent weight range at that height
(in the user's display unit) — "Normal weight" as a bare BMI number
range doesn't say much on its own, but "59.9–81.0 kg" does. `show_bmi`
lets a user turn the whole card off regardless of whether it's
computable — nothing else in the app reads either field.

Also carries `language` — the UI language (one of the six shipped
locales, see `ARCHITECTURE.md` "Internationalization"), applied by
`apps.accounts.middleware.UserLanguageMiddleware`. Distinct from
`unit_system`/`timezone`: it changes what language the interface reads
in, not what units or "today" mean.

Also carries `show_achievements` — unlike every other boolean toggle on
this model, this is a *privacy* setting rather than a personal display
preference: the dashboard's achievements carousel and "Recently active"
list (`apps.analytics.achievements`, `UI.md` "Achievements carousel" /
"Recently active" list) are both shared across every user on the
instance, so this controls whether *this* user's own data — longest
streak/workout count/PRs/total weight lifted, and when they last
started a workout — is included in what everyone sees, not whether they
personally see either widget — turning it off doesn't hide them from
their owner, it hides the owner's own data from everyone, themselves
included.

## Exercise

An exercise represents a movement.

Fields should support:
- name
- description
- primary muscle groups
- secondary muscle groups
- equipment
- movement type
- active/inactive
- system/user ownership where applicable

Users can create custom exercises.

An exercise also carries a `weight_input_mode` (e.g. total load vs.
per-hand/dumbbell) so logging and progression math use a consistent
convention per exercise rather than a single global setting.

Anything a workout can historically reference (exercises, equipment,
activity types) is soft-deleted via an `active` flag rather than hard
deleted, so old history keeps rendering correctly after a user deactivates
something they no longer use.

A custom exercise (`owner` set) is visible and editable only by the user who
created it — same cross-user privacy rule as programs/workouts/measurements
(see `ARCHITECTURE.md` → Security). System exercises (`owner` null) are
visible to everyone and only editable via the admin. Exercise names are
unique among system exercises, and unique per-user among a user's own
custom exercises — two different users may each name a custom exercise the
same thing.

25 system exercises are seeded (`apps.exercises` migrations 0002, 0004),
covering all 11 muscle groups across barbell/dumbbell/machine/cable/
bodyweight/kettlebell equipment. System exercise (and `MuscleGroup`/
`Equipment`) names are translated for display — the stored name always
stays canonical English — see `ARCHITECTURE.md` → "Internationalization"
for how.

## MuscleGroup

Examples:
- Chest
- Back
- Shoulders
- Biceps
- Triceps
- Quads
- Hamstrings
- Glutes
- Calves
- Abs
- Forearms

An exercise may target multiple muscle groups.

## Program

A reusable training program.

A program contains workouts such as:

```text
Program
├── Workout A
├── Workout B
└── Workout C
```

A program may optionally have a schedule.

`is_template` marks a program as copyable rather than meant to be run
directly — true for every built-in system program (`owner` null,
seeded: a generic full-body split plus several well-known named
programs — Arnold Split, Push/Pull/Legs, 5×5 Strength, Upper/Lower
Split, German Volume Training — 6 total, `apps.programs` migrations
0002/0004/0006). Names/descriptions/workout names of these built-in
templates are translated for display the same way system exercise names
are (`ARCHITECTURE.md` → "Internationalization") — the stored value
always stays canonical English. `is_template` is, on request,
settable by a user on their own programs too, so someone can keep a
personal template (e.g. "My PPL Template") and copy it into a fresh,
independently-editable program each time they start a new training
block, leaving the template itself untouched. Copying (`services.copy_program`)
works the same way regardless of whether the source is a system or
personal template — it only ever depends on the copying user being able
to see the source program at all (`services.visible_to`), never on
`is_template` itself, which is a UI affordance for the owner, not a
visibility grant.

## Workout

A planned workout inside a program.

It contains exercise prescriptions.

Example:

```text
Workout A
├── Bench Press
├── Overhead Press
└── Triceps Pushdown
```

## ExercisePrescription

Defines what the program expects for an exercise.

Potential fields:
- exercise
- set count
- minimum reps
- maximum reps
- target RPE
- target RIR
- progression method
- weight increment
- percentage target
- ordering
- notes

## Program versioning: snapshot-on-start

Changing a program must not change completed workout history. This is
achieved by **snapshotting**, not by a `ProgramVersion` table: see
`ARCHITECTURE.md` → "Historical integrity mechanism" for the full rationale.

In practice: `Program` carries a display-only `version` integer and
`updated_at`. `WorkoutSession` and its performed exercises/sets each copy the
prescription values they were created from (see below) instead of
dereferencing `Program`/`Workout`/`ExercisePrescription` at read time.

## WorkoutSession

Represents an actual attempt at a planned workout.

States:
- planned
- in_progress
- completed
- abandoned

Fields include:
- user
- program/workout reference (informational link only, not used to compute
  history — see snapshot-on-start above)
- start time
- end time
- status

A user can permanently delete their own session (any status, including
completed) — a deliberate escape hatch for a mistaken or unwanted logged
workout, distinct from `abandon`'s soft "still in history, marked
abandoned" state. Only reachable from the session's own detail page, not
the history list, so it's never one accidental tap away while browsing.

While `status` is `in_progress`, the session is also reachable via
"training mode" (`UI.md` "Training mode") — a focused,
one-exercise-at-a-time alternative to the full detail page, linked from a
floating button shown on every page. No new fields or state on the model
itself: training mode's "current exercise" and rest timer are both
computed/client-side, not persisted.

## PerformedExercise

Represents one exercise within a `WorkoutSession`, holding the **snapshot**
of what was prescribed at the moment the session was created:
- exercise reference
- snapshotted set count, rep range, target weight, progression method,
  weight increment, ordering, notes
- link to the `ExercisePrescription` it was created from (informational
  only — may later be edited, deactivated, or deleted without affecting
  this record)

## ExerciseSet

Represents an actual performed set, belonging to a `PerformedExercise`.

At minimum:
- exercise
- set number
- weight (`DecimalField`, canonical kg)
- reps
- target reps
- timestamp
- RPE, optional
- RIR, optional
- failure flag
- `is_warmup` flag — warmup sets are excluded from PR, progression, and
  analytics calculations by default
- notes

The performed set is historical data.

## Activity

Represents non-gym activity.

Fields:
- type
- date
- start time
- duration
- distance
- calories, optional
- notes
- user

Users may create custom activity types.

Implemented as `ActivityType` (a starting set of common types seeded, not
an exhaustive doc-specified list like `MeasurementType` — users are
expected to add their own) plus `Activity`. `date` and `start_time` are
kept as separate fields rather than one combined timestamp: `start_time`
is optional, since logging "went for a run today" shouldn't require also
stating a precise clock time. `distance` is optional too (canonical
meters) — plenty of activity types have none — and converts to km/miles
the same way `BodyMeasurement`'s length readings convert to cm/inches.

The 8 seeded system activity type names are translated for display the
same way system exercise/program names are — see `ARCHITECTURE.md` →
"Internationalization"; the stored name always stays canonical English.

## BodyMeasurement

Stores a time-stamped body measurement.

Supported measurements:
- weight
- body fat %
- waist
- chest
- arm
- thigh
- hip
- neck

Users may add custom measurements.

Implemented as `MeasurementType` (system-seeded for the list above, plus
user-created custom types — same ownership/soft-delete pattern as
`Exercise`) with a `unit_kind` (weight/length/percentage) that decides how
`apps.measurements.units` converts a reading between canonical storage
and the user's display unit: weight in kg/lb, length (circumferences) in
cm/inches, percentage unconverted. Canonical storage follows
`ARCHITECTURE.md`'s "meters for distance" rule even for circumferences,
at enough decimal places (0.1mm) that a cm/inch round-trip never loses
precision — display values are then rounded to what's actually worth
looking at (0.01 kg, 0.1 cm/inch) independently of that storage decision.

The 8 seeded system measurement type names are translated for display the
same way system exercise/program names are — see `ARCHITECTURE.md` →
"Internationalization", including that section's note on "Body fat %"
and why `{% trans %}` alone isn't enough for content containing a "%".

## PR

PRs should be derived from historical performance and/or stored as immutable achievement records where useful.

Types:
- max weight
- rep PR
- rep-specific PR
- estimated 1RM
- set volume
- session volume

Do not make PR logic depend on the current program definition.
