"""Activity visibility, summary stats, and chart data prep — kept out of
views/templates per CLAUDE.md ("do not put analytics logic in
templates"). This is Phase 9's "activity analytics"; cross-activity-type
dashboards/trends are Phase 10.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q

from apps.core.charts import build_chart_series

from .models import Activity, ActivityType


def visible_to(user, *, include_inactive=False):
    """Activity types a user may log against: system types + their own."""
    qs = ActivityType.objects.filter(Q(owner__isnull=True) | Q(owner=user))
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs


def history_for(user, activity_type, limit=None):
    """A user's own logged activities of one type — never another user's,
    regardless of whether the type is system or custom."""
    qs = Activity.objects.filter(user=user, activity_type=activity_type)
    return qs[:limit] if limit else qs


@dataclass(frozen=True)
class ActivitySummary:
    count: int
    total_duration: timedelta
    total_distance: Decimal | None
    total_calories: int | None


def summarize(activities):
    activities = list(activities)
    distances = [a.distance for a in activities if a.distance is not None]
    calories = [a.calories for a in activities if a.calories is not None]
    return ActivitySummary(
        count=len(activities),
        total_duration=sum((a.duration for a in activities), timedelta()),
        total_distance=sum(distances) if distances else None,
        total_calories=sum(calories) if calories else None,
    )


def duration_chart_series(activities):
    """Duration (minutes) over time — the one metric every activity type
    has, unlike distance/calories which are optional."""
    readings = [
        (Decimal(activity.duration.total_seconds()) / Decimal(60), activity.date)
        for activity in activities
    ]
    return build_chart_series(readings)
