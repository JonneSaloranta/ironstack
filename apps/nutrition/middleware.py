from . import food_language


class FoodLanguageMiddleware:
    """Activates a logged-in user's `food_language` (see
    apps.nutrition.food_language) for this request. Same after-auth
    placement and "re-derive from the database every request" rule as
    apps.accounts.middleware.UserLanguageMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        code = ""
        if user is not None and getattr(user, "is_authenticated", False):
            code = getattr(user, "food_language", "")
        token = food_language.activate(code)
        try:
            return self.get_response(request)
        finally:
            food_language.deactivate(token)
