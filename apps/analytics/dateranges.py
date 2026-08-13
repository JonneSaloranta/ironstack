"""Date-range resolution for analytics — docs/ANALYTICS.md "Date ranges".

Kept out of views/templates: a view only needs to turn a GET param into a
`DateRange` and pass it to a service function.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

RANGE_CHOICES = [
    ("7d", _("7 days")),
    ("30d", _("30 days")),
    ("3m", _("3 months")),
    ("6m", _("6 months")),
    ("1y", _("1 year")),
    ("all", _("All time")),
]

_PRESET_DAYS = {"7d": 7, "30d": 30, "3m": 90, "6m": 182, "1y": 365}

DEFAULT_RANGE = "30d"


@dataclass(frozen=True)
class DateRange:
    key: str
    start: date | None  # None means no lower bound ("all time")
    end: date


def resolve(key: str | None, *, start: date | None = None, end: date | None = None) -> DateRange:
    """Resolve a preset key, or an explicit `start`/`end` pair for
    docs/ANALYTICS.md's "custom range" — an explicit `start` takes
    priority over any preset key it's passed alongside."""
    today = timezone.localdate()
    resolved_end = end or today

    if start is not None:
        return DateRange(key="custom", start=start, end=resolved_end)

    if key in _PRESET_DAYS:
        return DateRange(
            key=key, start=resolved_end - timedelta(days=_PRESET_DAYS[key]), end=resolved_end
        )

    return DateRange(key="all", start=None, end=resolved_end)
