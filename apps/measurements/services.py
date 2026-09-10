"""Measurement visibility, chart data prep, and reminder/summary logic,
kept out of views/templates per CLAUDE.md ("do not put analytics logic
in templates").
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from apps.core.charts import build_chart_series  # noqa: F401 — re-exported for callers

from .models import BodyMeasurement, MeasurementType

# How long a user can go without logging *any* body measurement before
# the dashboard nudges them — apps.nutrition.services'
# OPENFOODFACTS_STALENESS_DAYS is the same order of magnitude for a
# different kind of staleness; there's no evidence either cadence has
# to match the other, so this is its own constant rather than reusing
# that one.
BODY_TRACKING_REMINDER_DAYS = 14


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


def days_since_last_measurement(user):
    """Days since `user`'s most recent BodyMeasurement of *any* type —
    system or custom, doesn't matter which one they last touched.
    Falls back to `date_joined` for a user who's never logged one at
    all, so a brand-new account doesn't get nudged before it's even
    had `BODY_TRACKING_REMINDER_DAYS` to log a first reading."""
    latest = BodyMeasurement.objects.filter(user=user).order_by("-recorded_at").first()
    since = latest.recorded_at if latest is not None else user.date_joined
    return (timezone.now() - since).days


def needs_body_tracking_reminder(user):
    """Whether the dashboard should show its "Time to log your body
    measurements?" card for `user` — CLAUDE.md "Automation must never
    take control away from the user": this only ever surfaces that one
    passive, dismiss-by-ignoring card, never a blocking prompt, and
    `user.body_tracking_reminders_enabled` (Profile → Preferences →
    Notifications) turns it off entirely regardless of how stale their
    history is."""
    if not user.body_tracking_reminders_enabled:
        return False
    return days_since_last_measurement(user) >= BODY_TRACKING_REMINDER_DAYS


@dataclass(frozen=True)
class MeasurementStats:
    """Summary of a user's full logged history for one measurement
    type. Canonical units throughout, like everything else in this
    module — the view converts each field to the user's display unit,
    the same way it already converts `entry.display_value`/
    `chart_points` per reading."""

    entry_count: int
    latest_value: Decimal
    latest_recorded_at: object  # a datetime — "object" matching apps.core.charts.ChartPoint.date
    first_value: Decimal
    first_recorded_at: object
    change_since_first: Decimal
    min_value: Decimal
    max_value: Decimal
    average_value: Decimal


def stats_for(user, measurement_type):
    """None for fewer than 2 readings — a single entry has no history
    yet to summarize (no "change since first", no meaningful min/max/
    average beyond that one value)."""
    history = list(history_for(user, measurement_type))
    if len(history) < 2:
        return None
    values = [entry.value for entry in history]
    latest, first = history[0], history[-1]  # history_for: newest first (Meta.ordering)
    return MeasurementStats(
        entry_count=len(history),
        latest_value=latest.value,
        latest_recorded_at=latest.recorded_at,
        first_value=first.value,
        first_recorded_at=first.recorded_at,
        change_since_first=latest.value - first.value,
        min_value=min(values),
        max_value=max(values),
        average_value=sum(values) / len(values),
    )
