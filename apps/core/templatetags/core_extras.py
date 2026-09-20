"""Shared template filters — apps.core, matching its role as the home for
cross-cutting display formatting (see apps.core.units, apps.core.charts).
"""

from django import template
from django.utils.translation import gettext

from apps.core import units as core_units

register = template.Library()


@register.filter
def translate_content(value):
    """Translate a piece of seeded *content* (an exercise/program/
    measurement-type/activity-type name — see docs/ARCHITECTURE.md
    "Internationalization") whose value might contain a literal "%".

    The usual pattern for this is `{% trans someobj.name %}`, but Django's
    `{% trans %}` tag — when given a template *variable* rather than a
    string literal — doubles every "%" in the resolved value before
    passing it to gettext as the msgid, then undoes the doubling on the
    way back out (`django/templatetags/i18n.py`, "Restore percent
    signs" — meant for literal `%%` written by hand in template source
    to escape it from string-format interpolation, but applied
    unconditionally to variables too). For a value with a single, real
    "%" — e.g. the seeded MeasurementType name "Body fat %" — this looks
    up the catalog for "Body fat %%", finds nothing, and silently falls
    back to the untranslated English string. This filter calls
    `gettext()` directly on the resolved value instead, with no
    doubling, so it translates correctly regardless of "%" in the text.
    Prefer `{% trans someobj.name %}` for content known not to contain
    "%" (exercises, programs, muscle groups, equipment all currently
    don't); use this filter for anything that might.
    """
    if not value:
        return value
    return gettext(value)


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


@register.simple_tag
def url_replace(request, **kwargs):
    """Current query string with `kwargs` overridden — what a pager needs
    to change `page` while keeping search/filter/sort params, url-encoded
    and without emitting empty parameters the way hand-built
    `?page=N&q={{ query }}` links did."""
    params = request.GET.copy()
    for key, value in kwargs.items():
        params[key] = value
    for key in [k for k, v in params.items() if v == ""]:
        del params[key]
    return params.urlencode()


@register.filter
def field_a11y(bound_field):
    """Render a bound form field with `aria-describedby` pointing at its
    help/error paragraphs (ids used by templates/core/_field.html) and
    `aria-invalid` when it has errors, so assistive tech announces the
    error when the control is focused."""
    described = []
    if bound_field.help_text:
        described.append(f"{bound_field.auto_id}_help")
    if bound_field.errors:
        described.append(f"{bound_field.auto_id}_error")
    attrs = {}
    if described:
        attrs["aria-describedby"] = " ".join(described)
    if bound_field.errors:
        attrs["aria-invalid"] = "true"
    return bound_field.as_widget(attrs=attrs)
