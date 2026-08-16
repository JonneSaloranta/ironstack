"""Weight-trend analysis — see docs/NUTRITION.md "Dynamic calorie
adjustment". Pure functions over plain `(date, weight_kg)` readings, no
DB dependency — testable against synthetic histories without touching
apps.measurements at all. apps.nutrition.suggestions is the layer that
actually reads real BodyMeasurement rows and calls into this module.
"""

from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

MOVING_AVERAGE_WINDOW_DAYS = 7
# Never react to a single reading (docs/NUTRITION.md's own explicit
# rule, echoing docs/PROGRESSION.md's "failure is a signal, not a
# command") — no suggestion is attempted below this much history.
MIN_SPAN_DAYS = 14
MIN_DISTINCT_DAYS = 4

RATE_PLACES = Decimal("0.001")


def bucket_by_day(
    readings: list[tuple[date_type, Decimal]],
) -> dict[date_type, Decimal]:
    """Same-day multiple weigh-ins are averaged into one point per
    day, so logging twice in a morning doesn't double-weight that
    day in the trend."""
    by_day: dict[date_type, list[Decimal]] = {}
    for day, weight in readings:
        by_day.setdefault(day, []).append(weight)
    return {day: sum(values) / len(values) for day, values in by_day.items()}


def moving_average_trend(
    readings: list[tuple[date_type, Decimal]], *, window_days: int = MOVING_AVERAGE_WINDOW_DAYS
) -> list[tuple[date_type, Decimal]]:
    """One `(date, trend_weight)` point per day that has a real
    reading, each averaged over the `window_days` calendar days ending
    on it, using whatever real readings actually fall in that window —
    sparse logging just means a smaller effective average that day,
    not a gap in the trend line."""
    by_day = bucket_by_day(readings)
    if not by_day:
        return []
    trend = []
    for day in sorted(by_day):
        window_start = day - timedelta(days=window_days - 1)
        window_values = [w for d, w in by_day.items() if window_start <= d <= day]
        trend.append((day, sum(window_values) / len(window_values)))
    return trend


@dataclass(frozen=True)
class TrendResult:
    actual_rate_kg_per_week: Decimal
    span_days: int
    distinct_days: int
    trend_start_weight: Decimal
    trend_end_weight: Decimal


def compute_trend(readings: list[tuple[date_type, Decimal]]) -> TrendResult | None:
    """`None` if there isn't yet enough history to say anything —
    fewer than `MIN_DISTINCT_DAYS` distinct logging days, or less than
    `MIN_SPAN_DAYS` between the first and last. The caller
    (apps.nutrition.suggestions) turns that into an explicit
    "insufficient data" result rather than guessing."""
    by_day = bucket_by_day(readings)
    if len(by_day) < MIN_DISTINCT_DAYS:
        return None
    sorted_days = sorted(by_day)
    span_days = (sorted_days[-1] - sorted_days[0]).days
    if span_days < MIN_SPAN_DAYS:
        return None

    trend = moving_average_trend(readings)
    start_weight = trend[0][1]
    end_weight = trend[-1][1]
    weeks = Decimal(span_days) / 7
    actual_rate = (end_weight - start_weight) / weeks

    return TrendResult(
        actual_rate_kg_per_week=actual_rate.quantize(RATE_PLACES, rounding=ROUND_HALF_UP),
        span_days=span_days,
        distinct_days=len(by_day),
        trend_start_weight=start_weight,
        trend_end_weight=end_weight,
    )
