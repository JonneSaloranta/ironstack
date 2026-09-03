"""Query helpers for exercise visibility.

Kept out of views per CLAUDE.md ("keep business/domain logic out of Django
views") even though the rule here is simple — this is the one place that
decides which exercises a user is allowed to see/use, so later phases
(programs, workouts) can reuse it instead of re-deriving the same filter.
"""

from django.db.models import Q
from django.utils.translation import gettext

from .models import Exercise


def visible_to(user, *, include_inactive=False):
    """Exercises a user may browse/prescribe: system exercises + their own."""
    qs = Exercise.objects.filter(
        Q(owner__isnull=True) | Q(owner=user)
    ).select_related("equipment")
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs


def search(queryset, query):
    """Exercise names are stored in English (the seeded library's own
    `name` values — apps.exercises.views used to `.filter(name__
    icontains=query)` directly against those), but every one of them
    is also a real gettext msgid, translated for display wherever it's
    shown via `{% trans %}`/`|translate_content` (see that filter's own
    docstring, apps.core.templatetags.core_extras — "Bent-over Row" ->
    "Kulmasoutu tangolla" for a Finnish user). A plain `icontains`
    against the stored English name can never match what a non-English
    user actually *sees* and types back — searching "kulmasoutu" found
    nothing, even for a user staring at an exercise literally labeled
    that on screen.

    Matches against *either* the raw stored name (still needed — an
    exercise search should also work in English regardless of the
    active language, and covers a custom exercise a user named in
    their own language to begin with, which was never a msgid at all)
    or its gettext-translated form. Done in Python rather than at the
    database level: gettext has no SQL-level equivalent to push this
    filter down into, and the exercise library `visible_to` already
    scopes this to (system exercises + one user's own) is small enough
    — dozens, not thousands of rows — that pulling `pk`/`name` for all
    of them and filtering in Python is the pragmatic answer, not a
    performance risk here specifically. Deliberately not applied the
    same way to apps.nutrition's own name searches (Food/Recipe), which
    can plausibly hold far more rows (an OpenFoodFacts-synced catalog)
    — see docs/DEVELOPMENT_LOG.md for that scope decision.
    """
    query = query.strip()
    if not query:
        return queryset
    query_lower = query.lower()
    matching_ids = [
        pk
        for pk, name in queryset.values_list("pk", "name")
        if query_lower in name.lower() or query_lower in gettext(name).lower()
    ]
    return queryset.filter(pk__in=matching_ids)
