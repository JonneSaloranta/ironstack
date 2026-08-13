"""Shared template filters — apps.core, matching its role as the home for
cross-cutting display formatting (see apps.core.units, apps.core.charts).
"""

from django import template

from apps.core import units as core_units

register = template.Library()


@register.filter
def duration(value):
    """Format a `timedelta` as e.g. "1h 15min" / "45min" / "<1min".

    A raw `{{ some_timedelta }}` renders via Python's default str(), e.g.
    "0:03:19.893476" — real seconds/microseconds from whenever a workout
    session was actually started/completed, which is meaningless noise
    for a training-time stat (docs/ANALYTICS.md). Round to the nearest
    whole minute instead, the smallest unit worth showing here.
    """
    if value is None:
        return ""
    total_minutes = round(value.total_seconds() / 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}min"
    if hours:
        return f"{hours}h"
    return f"{minutes}min"


@register.filter
def weight(value, user):
    """Format a canonical-kg `Decimal` for display in `user`'s preferred
    unit system.

    Regression: workout sets, PRs, prescriptions, and analytics totals
    were all rendered with a hardcoded " kg" suffix and no conversion,
    so an imperial-preference user saw raw kilograms everywhere outside
    apps.measurements (which already converted correctly) — see
    apps.core.units for the shared conversion dispatch this wraps.
    """
    if value is None:
        return ""
    unit_system = getattr(user, "unit_system", "metric")
    display = core_units.kg_to_display(value, unit_system)
    return f"{display} {core_units.weight_unit_label(unit_system)}"
