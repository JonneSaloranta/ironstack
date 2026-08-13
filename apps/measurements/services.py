"""Measurement visibility and chart data prep, kept out of views/templates
per CLAUDE.md ("do not put analytics logic in templates").
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q

from .models import BodyMeasurement, MeasurementType

TWO_PLACES = Decimal("0.01")


def visible_to(user, *, include_inactive=False):
    """Measurement types a user may track: system types + their own."""
    qs = MeasurementType.objects.filter(Q(owner__isnull=True) | Q(owner=user))
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs


def history_for(user, measurement_type, limit=None):
    """A user's own readings for one type, most recent first — never
    another user's, regardless of whether the type is system or custom."""
    qs = BodyMeasurement.objects.filter(user=user, measurement_type=measurement_type)
    return qs[:limit] if limit else qs


def latest_for(user, measurement_type):
    return history_for(user, measurement_type, limit=1).first()


@dataclass(frozen=True)
class ChartPoint:
    x: Decimal
    y: Decimal
    value: Decimal
    date: object


@dataclass(frozen=True)
class ChartSeries:
    points: list
    polyline: str
    min_value: Decimal
    max_value: Decimal
    width: int
    height: int


def build_chart_series(measurements, *, width=600, height=200, padding=20):
    """Normalize a list of BodyMeasurement (any order) into SVG-ready
    coordinates, oldest first. Returns None for fewer than 2 points — a
    single dot isn't a trend line."""
    ordered = sorted(measurements, key=lambda m: m.recorded_at)
    if len(ordered) < 2:
        return None

    values = [m.value for m in ordered]
    min_value, max_value = min(values), max(values)
    span = max_value - min_value or Decimal("1")  # avoid /0 when every value is equal
    plot_width = Decimal(width - 2 * padding)
    plot_height = Decimal(height - 2 * padding)
    last_index = len(ordered) - 1

    points = []
    for i, measurement in enumerate(ordered):
        x = Decimal(padding) + (Decimal(i) / Decimal(last_index)) * plot_width
        y = Decimal(height - padding) - ((measurement.value - min_value) / span) * plot_height
        points.append(
            ChartPoint(
                x=x.quantize(TWO_PLACES),
                y=y.quantize(TWO_PLACES),
                value=measurement.value,
                date=measurement.recorded_at,
            )
        )

    return ChartSeries(
        points=points,
        polyline=" ".join(f"{p.x},{p.y}" for p in points),
        min_value=min_value,
        max_value=max_value,
        width=width,
        height=height,
    )
