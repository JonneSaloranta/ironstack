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

## Rep Range Progression

Listed as its own method (distinct from Double Progression above) but not
otherwise elaborated on originally — implemented as double progression's
more patient sibling: instead of increasing off a single top-of-range
session, it requires the same session to repeat (two sessions in a row at
the top of the range, same weight) before recommending more weight. This
is the concrete form "recent performance trend should be considered"
takes here — apply it on the upside; a single strong session should still
count immediately for Double Progression (matching the 12/12/12 example
above), while Rep Range Progression is the deliberately slower option for
exercises where a fluke session shouldn't trigger a jump. Both still fall
back to maintaining or deloading the same way Linear Progression does.

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

Implemented as: one missed/failed session at a weight just maintains
(try again); two *consecutive* missed/failed sessions at the *same*
weight escalates to a deload (10% off). A single bad session is never
enough on its own — matches "failure is a signal, not an automatic
command." Applies to Linear, Double Progression, Rep Range, and
Maintenance; RPE/RIR uses its own actual-vs-target comparison instead
(see above) since it has a more direct signal to work from.

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

Implemented as `apps.progression.engine.calculate_progression(user,
prescription)`, returning a `ProgressionResult` (`action`,
`suggested_weight`, `reason`, `sessions_considered`, `one_rm_source`) —
`exercise_history`/`progression_settings` didn't need to be separate
parameters once `prescription` (which already carries every progression
setting — method, increment, rep range, target weight/RPE/RIR,
percentage target) and `user` (from which history is looked up directly)
were available, per "keep the actual API clean and idiomatic for the
project" above.

Keep the actual API clean and idiomatic for the project.

## Requirements

Progression logic must be:
- deterministic where inputs are the same
- explainable
- testable
- independent of HTTP
- independent of templates
