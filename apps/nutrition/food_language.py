"""Which language food names and ingredient lists are shown in.

Separate from the UI language on purpose: a user can run the app in
English but want their foods listed with the Finnish names on the
packages they buy (`User.food_language`, set on the profile page). Blank
means "same as the UI language".

Same shape as Django's own `translation.activate()`: the active value
lives in a context variable, set per request by
`apps.nutrition.middleware.FoodLanguageMiddleware`, so `Food.display_name`
and friends need no request passed around. Anything rendered outside a
request (a management command, the background refresh worker) simply
follows the active UI language.
"""

import contextvars
from contextlib import contextmanager

from django.conf import settings
from django.utils.translation import get_language

_active = contextvars.ContextVar("food_language", default=None)

# Food translations are stored for exactly the languages the app itself
# supports - the same list a user picks their food language from.
LANGUAGE_CODES = [code for code, _name in settings.LANGUAGES]


def _base(code):
    return (code or "").split("-")[0].lower()


def get_food_language():
    """The active food language's base code ("fi", "en", ...)."""
    return _active.get() or _base(get_language()) or _base(settings.LANGUAGE_CODE)


def activate(code):
    """Sets the food language for the current context; blank or `None`
    means "follow the UI language". Returns a token for `deactivate`."""
    return _active.set(_base(code) or None)


def deactivate(token):
    _active.reset(token)


@contextmanager
def override(code):
    token = activate(code)
    try:
        yield
    finally:
        deactivate(token)


def for_user(user):
    """The food language a given user sees, outside the request cycle
    (the API, where token auth runs after middleware)."""
    return _base(getattr(user, "food_language", "")) or _base(getattr(user, "language", ""))


def localized(translations, fallback, language=None):
    """`translations[language]` when present and non-blank, else
    `fallback` - the food's own canonical text."""
    language = language or get_food_language()
    value = (translations or {}).get(language) or ""
    return value.strip() or fallback
