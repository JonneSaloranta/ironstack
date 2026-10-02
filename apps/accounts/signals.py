"""Login-time housekeeping for User preferences."""

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import translation


def browser_language(request):
    """The supported language the browser asks for, or None when it
    doesn't say (no Accept-Language header, e.g. scripts and tests)."""
    if request is None or not request.META.get("HTTP_ACCEPT_LANGUAGE"):
        return None
    return translation.get_language_from_request(request).split("-")[0]


@receiver(user_logged_in)
def follow_browser_language(sender, request, user, **kwargs):
    """Until a user has picked a language themselves (`language_chosen`),
    every login sets `language` from the browser, so the app starts in
    the language the browser asks for. An explicit choice is never
    overridden."""
    if user.language_chosen:
        return
    code = browser_language(request)
    if code and code != user.language:
        user.language = code
        user.save(update_fields=["language"])
