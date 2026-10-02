"""Which tour a page shows, and remembering who has seen which."""

from .models import TutorialCompletion


def tours_by_view_name():
    from .tours import TOURS

    return {name: tour for tour in TOURS for name in tour.view_names}


def tour_by_key(key):
    from .tours import TOURS

    return next((tour for tour in TOURS if tour.key == key), None)


def completed_keys(user):
    return set(TutorialCompletion.objects.filter(user=user).values_list("tour", flat=True))


def tour_to_show(request):
    """The tour to run on this page, or None.

    Runs automatically the first time a user opens a page that has one,
    as long as they haven't switched tutorials off and aren't in the
    middle of onboarding (one overlay at a time). `?tour=<key>` (the
    profile's Tutorials page "Start" button) runs it regardless."""
    user = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    if user is None or not user.is_authenticated or match is None:
        return None
    tour = tours_by_view_name().get(match.view_name)
    if tour is None:
        return None
    if request.GET.get("tour") == tour.key:
        return tour
    if not user.tutorials_enabled or not user.onboarding_completed:
        return None
    if TutorialCompletion.objects.filter(user=user, tour=tour.key).exists():
        return None
    return tour


def mark_completed(user, key):
    TutorialCompletion.objects.get_or_create(user=user, tour=key)


def reset(user, key=None):
    """Re-arms one tour (or all of them) to start again by itself."""
    completions = TutorialCompletion.objects.filter(user=user)
    if key is not None:
        completions = completions.filter(tour=key)
    completions.delete()
