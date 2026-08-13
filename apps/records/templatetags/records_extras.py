"""Template filters for displaying `PersonalRecord` figures — thin
wrappers around apps.records.services' display-unit conversion, kept out
of apps.core.templatetags since the record-type-aware decision (weight
vs. rep count) is a records-app concept, not a generic one.
"""

from django import template

from apps.records import services as records_services

register = template.Library()


@register.filter
def pr_value(record, user):
    """See apps.records.services.format_value."""
    return records_services.format_value(record, user)


@register.filter
def pr_previous_value(record, user):
    """See apps.records.services.format_previous_value."""
    return records_services.format_previous_value(record, user)
