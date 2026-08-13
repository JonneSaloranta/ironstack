# Progression System

## Goal

The progression system determines how a planned exercise should progress over time.

Progression is domain logic and must be independent of views/templates.

Initial methods:

1. Double Progression
2. Linear Progression
3. Percentage Based
4. RPE/RIR Based
5. Rep Range Progression
6. Maintenance
7. Manual

Progression can be configured per exercise prescription.

## Double Progression

Example:

```text
3 sets
8–12 reps
80 kg
2.5 kg increment
```

If the user repeatedly reaches the top of the rep range, suggest increasing the load.

Example:

```text
12 / 12 / 12 @ 80 kg
```

may result in:

```text
82.5 kg
```

Do not base the decision on a single simplistic rule when enough history exists. Recent performance trend should be considered.

## Linear Progression

Increase load according to a configured increment.

The system should detect when progression is no longer succeeding and may recommend:
- maintaining load
- reducing load
- deloading

Do not automatically force a change.

## Percentage Based

Example:

```text
3 × 5 @ 80% 1RM
```

The 1RM source must be explicit:
- manually entered 1RM
- latest PR
- estimated 1RM

Show the user which source was used.

## RPE/RIR Based

Use RPE/RIR only when the user has supplied those values.

Example:

```text
Target RIR: 2
Actual RIR: 4
```

This may support increasing load.

Example:

```text
Target RIR: 2
Actual RIR: 0
```

This may support maintaining or reducing load.

Never fabricate RPE/RIR.

## Maintenance

Maintenance deliberately avoids continuous progression.

The goal is to maintain current performance with sensible loading.

Example:

```text
Maintain 80 kg
Target 8–10 reps
```

## Manual

The user controls the next weight/target.

The system should still record history and PRs.

## Failure handling

Failure is a signal, not an automatic command.

Repeated failure may result in:
- maintain
- reduce
- deload suggestion

The user remains in control.

## API/service concept

A progression service should be independently testable.

Conceptually:

```python
calculate_progression(
    exercise_history,
    prescription,
    progression_settings,
)
```

Keep the actual API clean and idiomatic for the project.

## Requirements

Progression logic must be:
- deterministic where inputs are the same
- explainable
- testable
- independent of HTTP
- independent of templates
