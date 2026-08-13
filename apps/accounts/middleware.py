from django.utils import translation


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
