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
