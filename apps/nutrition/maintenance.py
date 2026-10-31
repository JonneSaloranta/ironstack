"""An estimate of the user's real maintenance calories from what they ate
and what their weight did - see docs/NUTRITION.md "Observed maintenance".
Same shape as apps.nutrition.suggestions: a frozen dataclass, one public
entry point, nothing persisted. Information only: it never changes a
target and doesn't feed the adjustment suggestion.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone
from django.utils.formats import number_format
from django.utils.translation import gettext as _

from apps.core import units as core_units

from . import services, trends
from .energy import KCAL_PER_KG_BODY_FAT
from .suggestions import Confidence, weight_readings_since

WINDOW_DAYS = 28
MIN_FOOD_DAYS = 14
ROUNDING_KCAL = Decimal("50")
# Outside this, the arithmetic is almost certainly reflecting partly logged
# food or a water-weight swing rather than anyone's real energy use.
PLAUSIBLE_KCAL = (1000, 6000)


@dataclass(frozen=True)
class MaintenanceEstimate:
    """`calories` and `confidence` are None when there isn't enough
    trustworthy data yet; `reason` then says what's missing."""

    calories: int | None
    confidence: Confidence | None
    reason: str


def _confidence_for(food_days, weight_days, logging_gaps) -> Confidence:
    if logging_gaps:
        return Confidence.LOW
    if food_days >= 24 and weight_days >= 10:
        return Confidence.HIGH
    if food_days >= 18 and weight_days >= 6:
        return Confidence.MEDIUM
    return Confidence.LOW


def estimate_maintenance(user) -> MaintenanceEstimate:
    end = timezone.localdate() - timedelta(days=1)
    start = end - timedelta(days=WINDOW_DAYS - 1)
    stats = services.nutrition_stats(user, days=WINDOW_DAYS)
    if stats.days_logged < MIN_FOOD_DAYS:
        return MaintenanceEstimate(
            None,
            None,
            _(
                "Not enough logged food yet: %(logged)s of the %(needed)s days needed in the "
                "last %(window)s days."
            )
            % {"logged": stats.days_logged, "needed": MIN_FOOD_DAYS, "window": WINDOW_DAYS},
        )

    since = timezone.make_aware(datetime.combine(start, time.min))
    readings = [
        (day, weight) for day, weight in weight_readings_since(user, since) if day <= end
    ]
    trend = trends.compute_trend(readings)
    if trend is None:
        return MaintenanceEstimate(
            None,
            None,
            _(
                "Not enough logged weight yet: it takes at least %(days)s weigh-in days over "
                "%(span)s days."
            )
            % {"days": trends.MIN_DISTINCT_DAYS, "span": trends.MIN_SPAN_DAYS},
        )

    rate = trend.actual_rate_kg_per_week
    raw = stats.mean_calories - rate / 7 * KCAL_PER_KG_BODY_FAT
    calories = int(
        (raw / ROUNDING_KCAL).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * ROUNDING_KCAL
    )
    if not PLAUSIBLE_KCAL[0] <= calories <= PLAUSIBLE_KCAL[1]:
        return MaintenanceEstimate(
            None,
            None,
            _(
                "The logged food and weight don't add up to a believable figure yet. Some days "
                "may be only partly logged."
            ),
        )

    reason = _(
        "You ate %(intake)s kcal/day on average while your weight changed %(rate)s %(unit)s/week "
        "over the last %(days)s days."
    ) % {
        "intake": number_format(stats.mean_calories),
        "rate": number_format(
            core_units.kg_to_display(rate, user.unit_system).quantize(Decimal("0.01"))
        ),
        "unit": core_units.weight_unit_label(user.unit_system),
        "days": trend.span_days,
    }
    if stats.possible_logging_gaps:
        reason = "%s %s" % (
            reason,
            _("Some days may be only partly logged, which makes this estimate too low."),
        )
    return MaintenanceEstimate(
        calories,
        _confidence_for(stats.days_logged, trend.distinct_days, stats.possible_logging_gaps),
        reason,
    )
