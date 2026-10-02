"""The shapes every guided tour is built from — the tours themselves live
in `apps.tutorials.tours`. See docs/TUTORIALS.md for how to add or change
one without breaking it."""

from dataclasses import dataclass, field

from django.urls import reverse


@dataclass(frozen=True)
class Step:
    """One stop on a tour.

    `anchor` names the element to highlight: the
    page marks it with `data-tour="<anchor>"`. None means a centered card
    with nothing highlighted (an intro or a closing note).

    `optional=True` is for an element that legitimately isn't always on
    the page (an empty list, a staff-only control, a feature the user
    turned off). The tour silently skips a step whose element isn't
    there, but only optional steps are allowed to be missing in
    apps.tutorials.tests — a required one that disappears fails the
    suite instead of quietly vanishing from the tour."""

    anchor: str | None
    title: str
    body: str
    optional: bool = False


@dataclass(frozen=True)
class Tour:
    """A page's tour. `key` is stored in TutorialCompletion, so never
    rename one that has shipped (that would re-show it to everyone).

    `view_names` are the namespaced URL names (request.resolver_match.
    view_name) the tour runs on. `start_url(user)` returns a URL for the
    profile's Tutorials page "Start" button — the page's own URL for a
    page without arguments, or one of the user's own objects for a
    detail page (None when they have none yet)."""

    key: str
    section: str
    title: str
    view_names: tuple[str, ...]
    steps: tuple[Step, ...]
    start_url: object = None  # callable(user) -> str | None
    sample_kwargs: dict = field(default_factory=dict)

    def url_for(self, user):
        if self.start_url is not None:
            return self.start_url(user)
        return reverse(self.view_names[0])


def first_url(queryset_for_user, url_name, *, arg="pk"):
    """`start_url` for a detail page: the user's first matching object."""

    def _start_url(user):
        obj = queryset_for_user(user).first()
        return reverse(url_name, kwargs={arg: obj.pk}) if obj else None

    return _start_url
