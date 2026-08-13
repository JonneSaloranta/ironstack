# Domain Model

## User

Use Django's custom user model.

User-related data includes:
- authentication
- profile
- preferences
- preferred units

Internal values should use consistent canonical units. Convert for display.

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
