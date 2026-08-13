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
