"""Measurement visibility and chart data prep, kept out of views/templates
per CLAUDE.md ("do not put analytics logic in templates").
"""

from django.db.models import Q

from apps.core.charts import build_chart_series  # noqa: F401 — re-exported for callers

from .models import BodyMeasurement, MeasurementType


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
