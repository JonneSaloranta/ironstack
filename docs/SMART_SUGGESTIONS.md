# Smart Weight Suggestions

## Goal

Provide a useful, explainable recommendation for the next training load.

The system must never force the recommendation.

## Inputs

The suggestion engine may consider:

- recent exercise performance
- longer-term performance trend
- weight
- reps
- target reps
- RPE
- RIR
- failure
- exercise history
- progression method
- configured weight increment
- estimated 1RM
- recent PRs
- recent training load
- deload/maintenance state

Do not make the initial implementation unnecessarily complex.

Start with a deterministic, explainable rules-based engine. Keep the interface extensible so more sophisticated models could be added later.

## Output

A suggestion should contain conceptually:

```text
suggested_weight
target_min_reps
target_max_reps
confidence
reason
```

Confidence should be understandable and should not imply statistical certainty.

Suggested confidence levels can be:
- low
- medium
- high

## Examples

### Increase

```text
82.5 kg × 8–10

Reason:
You reached the top of the target rep range in
the last two sessions at 80 kg.
```

### Maintain

```text
80 kg × 8–10

Reason:
Performance has remained stable, but the target
rep range has not been consistently completed.
```

### Reduce

```text
77.5 kg × 8–10

Reason:
Performance has declined across recent sessions
and the previous load produced repeated failures.
```

## Explainability

Every recommendation should have a concise human-readable explanation.

Do not create a black-box recommendation that cannot explain itself.

## Safety/product behavior

The user can always:
- accept the suggested weight
- edit the weight
- ignore the suggestion

Never prevent a user from entering a different value.

## Architecture

Keep:

```text
ProgressionEngine
```

separate from:

```text
WeightSuggestionEngine
```

Progression defines the intended method.

Suggestion logic determines a sensible next load from available evidence.

## Insufficient history

When insufficient data exists, fall back to:
- program prescription
- manually configured starting weight
- simple progression rules

Do not pretend to have confidence when there is not enough history.

## Implementation

`apps.progression.suggestions.suggest_weight(user, prescription)` is
`WeightSuggestionEngine`. It composes `ProgressionEngine`
(`apps.progression.engine.calculate_progression`) with the prescription's
own configured rep range, adding only what the engine doesn't already
produce: `target_min_reps`/`target_max_reps` and a `confidence` level.
Confidence is a direct, deterministic function of how much evidence the
underlying decision used — never a black-box score:

- `insufficient_data` or `manual` action → low
- `calculated` (percentage-based) → high for a manual or PR-backed 1RM,
  medium for a live single-set estimate
- otherwise (trend-based increase/maintain/decrease/deload) → high with
  2+ supporting sessions, medium with 1, low with none

Reached from the UI in `apps.workouts.views._build_set_form`: a
performed exercise's very first set (only) gets its weight/reps
pre-filled from the suggestion, with the confidence and reason shown
alongside as plain, editable form defaults — never validated against,
never forced. `apps.workouts` cannot import `apps.progression` directly
(the dependency already runs the other way, since progression reads
workout history), so this composition happens at the view layer, the one
place allowed to cross that boundary — see `docs/ARCHITECTURE.md`.

The rendered banner (`templates/workouts/_performed_exercise_card.html`)
always leads with an explicit "Suggested:" label plus a confidence tag
before the weight/reps, so — unlike the charts covered in
`docs/ANALYTICS.md`'s "Chart titles/legends audit" — there's no
analogous "what am I looking at" gap here: this is plain labeled text,
not an SVG that could hide its meaning behind a screen-reader-only
`aria-label`. Checked as part of the same audit; nothing to fix.

The reason strings `apps.progression.engine` actually generates (e.g.
"Hit the top of your rep range for 2 sessions in a row at 80.00 kg —
adding 2.50 kg.") are worded differently from this doc's illustrative
Examples above ("You reached the top of the target rep range in the
last two sessions at 80 kg.") — same meaning, different phrasing. The
examples are illustrative, not a literal string spec; don't expect to
find their exact wording in the code.
