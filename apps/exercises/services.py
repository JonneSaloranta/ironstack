"""Query helpers for exercise visibility.

Kept out of views per CLAUDE.md ("keep business/domain logic out of Django
views") even though the rule here is simple — this is the one place that
decides which exercises a user is allowed to see/use, so later phases
(programs, workouts) can reuse it instead of re-deriving the same filter.
"""

from django.db.models import Q

from .models import Exercise


def visible_to(user, *, include_inactive=False):
    """Exercises a user may browse/prescribe: system exercises + their own."""
    qs = Exercise.objects.filter(
        Q(owner__isnull=True) | Q(owner=user)
    ).select_related("equipment")
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs
