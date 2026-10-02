from decimal import Decimal, InvalidOperation

from django import template

from apps.assistant import formatting, services
from apps.core.templatetags.core_extras import weight
from apps.programs.models import Weekday

register = template.Library()


@register.filter
def assistant_reply(text):
    return formatting.render_reply(text)


@register.filter
def weekday_name(value):
    """0-6 (0 = Monday, Python's own date.weekday()) → the translated day
    name the rest of the app uses (apps.programs.models.Weekday)."""
    try:
        return Weekday(int(value)).label
    except (TypeError, ValueError):
        return ""


@register.filter
def proposal_weight(value, user):
    """A proposal payload stores numbers as strings (JSON has no
    Decimal); this formats one in the user's own units like the core
    `weight` filter does for a real model field."""
    try:
        return weight(Decimal(str(value)), user)
    except (InvalidOperation, ValueError, TypeError):
        return ""


@register.filter
def has_new_foods(payload):
    return any(
        item.get("new_food") for meal in payload.get("meals", []) for item in meal.get("items", [])
    )


@register.inclusion_tag("assistant/_ask_link.html", takes_context=True)
def assistant_ask_link(context, prompt, label):
    """A secondary "ask the assistant" button for another app's page
    (the diet plan list, the program list), pre-filling the first
    message. Renders nothing for a user who couldn't use the assistant
    anyway — so the check, and its couple of queries, only happens on the
    pages that offer the link."""
    request = context.get("request")
    user = getattr(request, "user", None)
    show = bool(user and user.is_authenticated and services.is_available(user))
    return {"show": show, "prompt": prompt, "label": label}
