"""A varied, time-of-day-aware greeting for the profile page, replacing
the flat "Signed in as X" line. Mixes encouragement and light humor and
is picked at random on every render, so the same page can read
differently from one visit to the next.

Each candidate string is wrapped in `gettext_lazy` (not `gettext`) at
module import time — that's deliberate: `gettext_lazy` defers the
actual translation lookup until the string is coerced (here, by the
`%` formatting in `random_greeting`), so it always resolves against
whichever language is active *at request time*, not whatever happened
to be active when this module was first imported. Using plain
`gettext` here would translate every candidate once at import time
(usually into the server's default language) and never again. This is
also why the candidates live directly as `_("...")` calls rather than
being built from raw strings passed through a helper — `makemessages`
only extracts a literal string argument to a translation call, not a
variable, so every candidate needs its own literal `_("...")` call
site.
"""

import random

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

_MORNING_GREETINGS = (
    _("Good morning, %(username)s! Time to get after it."),
    _("Rise and grind, %(username)s — the weights aren't going to lift themselves."),
    _("Morning, %(username)s. Today's a great day for a new personal record."),
    _("Good morning, %(username)s. Coffee first, then conquer the day."),
)

_AFTERNOON_GREETINGS = (
    _("Good afternoon, %(username)s! Keep the momentum going."),
    _("Hey %(username)s, halfway through the day and still standing — not bad."),
    _("Afternoon, %(username)s. Perfect time to sneak in a workout."),
    _("Good afternoon, %(username)s. Lunch was earned, apparently."),
)

_EVENING_GREETINGS = (
    _("Good evening, %(username)s! One more push before the day's done."),
    _("Evening, %(username)s. A great time to log today's work."),
    _("Hey %(username)s, the evening session hits different."),
    _("Good evening, %(username)s — finish the day stronger than you started it."),
)

_NIGHT_GREETINGS = (
    _("Still up, %(username)s? Respect the hustle."),
    _("Good night, %(username)s — recovery is part of the program too."),
    _("Burning the midnight oil, %(username)s? Even legends need sleep."),
    _("Late night, %(username)s. Tomorrow's gains start with tonight's rest."),
)

_GREETINGS_BY_BUCKET = {
    "morning": _MORNING_GREETINGS,
    "afternoon": _AFTERNOON_GREETINGS,
    "evening": _EVENING_GREETINGS,
    "night": _NIGHT_GREETINGS,
}


def _time_bucket(hour):
    """Morning 05:00–11:59, afternoon 12:00–17:59, evening 18:00–22:59,
    night the rest — ordinary local-clock intuition, not tied to any
    particular workout schedule."""
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def random_greeting(user, *, now=None):
    """One random, translated greeting line for `user`, chosen from the
    pool matching the current time of day in the active timezone
    (`django.utils.timezone.localtime`, which respects whatever
    timezone `apps.accounts.middleware.TimezoneMiddleware` already
    activated for this request from the user's own preference — see
    that middleware's docstring)."""
    current = timezone.localtime(now)
    bucket = _time_bucket(current.hour)
    template = random.choice(_GREETINGS_BY_BUCKET[bucket])
    return template % {"username": user.username}
