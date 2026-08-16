"""The dynamic calorie-adjustment engine — see docs/NUTRITION.md
"Dynamic calorie adjustment". Mirrors apps.progression.suggestions'
exact shape: a frozen dataclass, one public entry point, no persisted
model (derive, don't cache — recomputed live every call).

Reads real weight history from apps.measurements (never a duplicate
copy of it — see docs/NUTRITION.md "Why a new app") and composes it
with apps.nutrition.trends' pure trend math. This is the one place in
apps.nutrition allowed to reach into apps.measurements for a specific
reason: the boundary-crossing rule apps.progression/apps.workouts
already established (docs/NUTRITION.md "Integration with existing
apps") — composition happens in whichever module is downstream of both
concerns, never inside the lower-level app itself.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from django.utils.translation import gettext as _

from apps.measurements.models import BodyMeasurement, MeasurementType

from . import trends
from .energy import KCAL_PER_KG_BODY_FAT
from .models import NutritionGoal, NutritionTarget

# Tolerance band: actual and target rate are considered "on track" if
# within this fraction of the target's own magnitude, with an absolute
# floor so a near-zero maintenance target isn't impossibly strict.
TOLERANCE_FRACTION = Decimal("0.3")
MIN_TOLERANCE_KG_PER_WEEK = Decimal("0.1")

# A suggested change is rounded to the nearest 25 kcal (false precision
# — "-137 kcal/day" — is exactly what docs/NUTRITION.md "Safety bounds"
# warns against) and capped per suggestion, so a large gap is corrected
# over more than one cycle rather than one big swing — the same
# "don't overreact" rule docs/PROGRESSION.md applies to deload timing.
ADJUSTMENT_ROUNDING_KCAL = Decimal("25")
MAX_SINGLE_ADJUSTMENT_KCAL = Decimal("250")


class AdjustmentAction(StrEnum):
    ON_TRACK = "on_track"
    ADJUST = "adjust"
    INSUFFICIENT_DATA = "insufficient_data"
    NO_ACTIVE_GOAL = "no_active_goal"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class AdjustmentSuggestion:
    action: AdjustmentAction
    target_rate_kg_per_week: Decimal | None
    actual_rate_kg_per_week: Decimal | None
    suggested_daily_calories: int | None
    delta_kcal: int | None
    confidence: Confidence | None
    reason: str


def _confidence_for(trend_result: trends.TrendResult) -> Confidence:
    """A direct, deterministic function of how much evidence backs the
    trend — never a black-box score (docs/SMART_SUGGESTIONS.md)."""
    if trend_result.distinct_days >= 10 and trend_result.span_days >= 21:
        return Confidence.HIGH
    if trend_result.distinct_days >= 6 and trend_result.span_days >= 14:
        return Confidence.MEDIUM
    return Confidence.LOW


def _weight_readings_for(user) -> list[tuple]:
    body_weight_type = MeasurementType.objects.filter(name="Body weight", owner=None).first()
    if body_weight_type is None:
        return []
    return [
        (recorded_at.date(), value)
        for recorded_at, value in BodyMeasurement.objects.filter(
            user=user, measurement_type=body_weight_type
        ).values_list("recorded_at", "value")
    ]


def suggest_calorie_adjustment(user) -> AdjustmentSuggestion:
    """The single entry point the dashboard calls. Never auto-applied —
    the caller presents this as a dismissible suggestion; accepting it
    is a separate, explicit action that creates a new NutritionTarget
    (source="adjusted")."""
    goal = NutritionGoal.objects.filter(user=user, ended_at__isnull=True).first()
    target = NutritionTarget.objects.filter(user=user, ended_at__isnull=True).first()
    if goal is None or target is None:
        return AdjustmentSuggestion(
            action=AdjustmentAction.NO_ACTIVE_GOAL,
            target_rate_kg_per_week=None,
            actual_rate_kg_per_week=None,
            suggested_daily_calories=None,
            delta_kcal=None,
            confidence=None,
            reason=_("Set a nutrition goal to get adjustment suggestions."),
        )

    trend_result = trends.compute_trend(_weight_readings_for(user))
    if trend_result is None:
        return AdjustmentSuggestion(
            action=AdjustmentAction.INSUFFICIENT_DATA,
            target_rate_kg_per_week=goal.target_rate_kg_per_week,
            actual_rate_kg_per_week=None,
            suggested_daily_calories=None,
            delta_kcal=None,
            confidence=None,
            reason=_(
                "Not enough logged weight yet — keep logging your weight regularly and "
                "check back in a couple of weeks."
            ),
        )

    target_rate = goal.target_rate_kg_per_week
    actual_rate = trend_result.actual_rate_kg_per_week
    confidence = _confidence_for(trend_result)
    tolerance = max(abs(target_rate) * TOLERANCE_FRACTION, MIN_TOLERANCE_KG_PER_WEEK)

    if abs(actual_rate - target_rate) <= tolerance:
        return AdjustmentSuggestion(
            action=AdjustmentAction.ON_TRACK,
            target_rate_kg_per_week=target_rate,
            actual_rate_kg_per_week=actual_rate,
            suggested_daily_calories=None,
            delta_kcal=None,
            confidence=confidence,
            reason=_("Target: %(target)s kg/week. Actual trend: %(actual)s kg/week — on track.")
            % {"target": target_rate, "actual": actual_rate},
        )

    shortfall = target_rate - actual_rate
    raw_delta_kcal = shortfall * KCAL_PER_KG_BODY_FAT / Decimal("7")
    rounded_delta = (raw_delta_kcal / ADJUSTMENT_ROUNDING_KCAL).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ) * ADJUSTMENT_ROUNDING_KCAL
    capped_delta = max(
        min(rounded_delta, MAX_SINGLE_ADJUSTMENT_KCAL), -MAX_SINGLE_ADJUSTMENT_KCAL
    )
    suggested_calories = target.daily_calories + int(capped_delta)

    reason = _(
        "Target: %(target)s kg/week. Actual trend: %(actual)s kg/week over the last "
        "%(days)s days. Suggested adjustment: %(delta)+d kcal/day."
    ) % {
        "target": target_rate,
        "actual": actual_rate,
        "days": trend_result.span_days,
        "delta": int(capped_delta),
    }

    return AdjustmentSuggestion(
        action=AdjustmentAction.ADJUST,
        target_rate_kg_per_week=target_rate,
        actual_rate_kg_per_week=actual_rate,
        suggested_daily_calories=suggested_calories,
        delta_kcal=int(capped_delta),
        confidence=confidence,
        reason=reason,
    )
