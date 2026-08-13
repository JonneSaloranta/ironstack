"""WeightSuggestionEngine — turns a ProgressionEngine decision into a full,
explainable suggestion a user can see, accept, edit, or ignore.

Kept as its own module, separate from engine.py, per
docs/SMART_SUGGESTIONS.md "Architecture": ProgressionEngine defines the
intended *method*; this module decides a sensible next *load* from the
evidence available and adds the two things a suggestion needs that a pure
progression decision doesn't — a confidence level and the target rep
range to display alongside the weight (which comes from the prescription,
not from progression math).

This module never talks to HTTP/templates directly (docs/SMART_SUGGESTIONS.md
requirements match docs/PROGRESSION.md's); it is called from
apps.workouts.views, which is where the suggestion actually reaches a
form as a pre-filled, freely-overridable default — see that module for
why: apps.progression already depends on apps.workouts (for history), so
apps.workouts itself can't depend back on apps.progression without a
cycle. Only the view layer is allowed to cross that boundary.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .engine import ProgressionAction, ProgressionResult, calculate_progression


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Suggestion:
    suggested_weight: Decimal | None
    target_min_reps: int | None
    target_max_reps: int | None
    confidence: Confidence
    reason: str
    action: ProgressionAction
    one_rm_source: str | None = None


_CALCULATED_CONFIDENCE_BY_SOURCE = {
    "manual": Confidence.HIGH,
    "latest_pr": Confidence.HIGH,
    "estimated": Confidence.MEDIUM,
}


def _confidence_for(result: ProgressionResult) -> Confidence:
    """Confidence should be understandable and never imply statistical
    certainty (docs/SMART_SUGGESTIONS.md) — it's a direct, deterministic
    function of how much evidence the underlying decision actually used,
    not a black-box score."""
    if result.action in (ProgressionAction.INSUFFICIENT_DATA, ProgressionAction.MANUAL):
        return Confidence.LOW
    if result.action == ProgressionAction.CALCULATED:
        return _CALCULATED_CONFIDENCE_BY_SOURCE.get(result.one_rm_source, Confidence.LOW)
    if result.sessions_considered >= 2:
        return Confidence.HIGH
    if result.sessions_considered == 1:
        return Confidence.MEDIUM
    return Confidence.LOW


def suggest_weight(user, prescription, *, manual_one_rm=None) -> Suggestion:
    """The single entry point the UI calls. Deterministic for the same
    inputs (delegates entirely to calculate_progression, which is itself
    deterministic) and always carries a human-readable `reason` — see
    docs/SMART_SUGGESTIONS.md "Explainability": never a black box.

    Insufficient history is not a special case here — it already falls
    back to the prescription's configured target_weight inside
    ProgressionEngine (docs/SMART_SUGGESTIONS.md "Insufficient history"),
    so this function doesn't need its own fallback chain.
    """
    result = calculate_progression(user, prescription, manual_one_rm=manual_one_rm)
    return Suggestion(
        suggested_weight=result.suggested_weight,
        target_min_reps=prescription.min_reps,
        target_max_reps=prescription.max_reps,
        confidence=_confidence_for(result),
        reason=result.reason,
        action=result.action,
        one_rm_source=result.one_rm_source,
    )
