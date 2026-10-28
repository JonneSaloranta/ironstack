# Personal Records (PR) System

## Goal

Automatically identify meaningful personal records.

## PR types

### Max Weight

Largest weight ever successfully recorded for an exercise.

### Rep PR

Highest rep count achieved at a given weight.

### Rep-specific PR

Track performance for common rep targets:

- 1RM
- 3RM
- 5RM
- 8RM
- 10RM
- 12RM

The implementation should remain extensible to arbitrary rep counts.

### Estimated 1RM

Calculate an estimated one-repetition maximum from suitable sets.

Do not hard-code the application to one formula. Put the calculation behind a service/interface so the formula can be changed later.

### Set Volume PR

```text
weight × reps
```

### Session Volume PR

Total relevant training volume for the exercise/session.

## PR detection

PR detection must be based on actual historical workout data, not the current program.

A program edit must never erase or rewrite previous PRs.

## Correcting a logged set

Records follow the user's own corrections to their set history. When a
set is edited or deleted, or a whole session is deleted (web or API),
`apps.records.services.rebuild_records` replays that exercise's
remaining sets in the order they were performed and brings the stored
records in line:

- a record that still holds keeps its row untouched
- a record its set no longer earns (deleted, lowered, marked warmup or
  failed) is removed
- a later set that only now turns out to have been a record - because
  the mistake it was compared against is gone - gets its record, without
  a "New PR" notification

So a mistyped 600 kg set, deleted right away, no longer stays the max
weight forever (issue #21).

## PR notifications

When a meaningful new PR is detected, display a clear notification.

Example:

```text
New PR

Bench Press
100 kg × 5

Estimated 1RM
116.7 kg

Previous estimated 1RM
112.5 kg
```

Avoid overwhelming the user with redundant notifications.

## Testing

Test:
- first recorded performance
- new max weight
- tied max
- higher reps at same weight
- rep-specific records
- estimated 1RM records
- volume records
- session records
- regression after program edits
- records after a set is edited or deleted, or its session is deleted
