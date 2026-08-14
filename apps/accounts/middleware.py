from django.utils import timezone, translation


class UserTimezoneMiddleware:
    """Applies a logged-in user's stored `timezone` preference (set on
    the profile page) as the active timezone for this request.

    Regression: nothing in the app ever called `timezone.activate()`
    anywhere — `user.timezone` was stored and validated on save, but
    every timezone-aware render (`django.utils.timezone.localdate`/
    `localtime`, the `{% now %}` tag, and — most visibly — every plain
    `{{ some_datetime|date:"..." }}` template filter on a stored
    datetime, which Django automatically converts to whatever timezone
    is *currently active* before formatting) silently used
    `settings.TIME_ZONE` ("UTC") for every user regardless of what
    they'd chosen. This also affects more than display: "this week"
    boundaries (`apps.core.views.DashboardView`,
    `apps.analytics.dateranges`) are computed from
    `timezone.localdate()`, so a user in a timezone behind/ahead of UTC
    could see a session logged just after their own midnight counted
    into the wrong week.

    Same "re-derive from the database every request, nothing cached
    elsewhere" pattern as `UserLanguageMiddleware` below — must sit
    after `AuthenticationMiddleware` (needs `request.user`) but still
    before the view/template rendering.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False) and user.timezone:
            try:
                timezone.activate(user.timezone)
            except Exception:
                # An unrecognized zone name shouldn't 500 the request —
                # ProfileForm validates against the real IANA list on
                # save, so this should never actually happen, but a
                # stale/hand-edited value falling back to
                # settings.TIME_ZONE is far better than a crash.
                pass

        response = self.get_response(request)
        timezone.deactivate()
        return response


class UserLanguageMiddleware:
    """Applies a logged-in user's stored `language` preference (set on
    the profile page) as the active UI language, overriding whatever
    `django.middleware.locale.LocaleMiddleware` guessed from the cookie/
    Accept-Language header.

    Must sit after `AuthenticationMiddleware` in `MIDDLEWARE` (needs
    `request.user`) but still runs before the view and template
    rendering, so `translation.activate()` here affects *this* request's
    own response. Nothing needs to be persisted for future requests —
    `user.language` in the database is the single source of truth, and
    this middleware re-derives the active language from it on every
    request rather than caching the choice anywhere else (a cookie or
    session key), so there's no second place that could drift out of
    sync with a profile change.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False) and user.language:
            translation.activate(user.language)
            request.LANGUAGE_CODE = translation.get_language()

        response = self.get_response(request)
        translation.deactivate()
        return response
