from rest_framework.permissions import BasePermission

_METHOD_TO_CRUD_FIELD = {
    "GET": "can_read",
    "HEAD": "can_read",
    "OPTIONS": "can_read",
    "POST": "can_create",
    "PUT": "can_update",
    "PATCH": "can_update",
    "DELETE": "can_delete",
}


class HasContextPermission(BasePermission):
    """Every apps.api view must both (a) be authenticated via a valid
    API key (apps.api.auth.ApiKeyAuthentication - there is no other way
    in) and (b) declare which `apps.api.models.ApiContext` it belongs to
    via an `api_context` class attribute, and (c) have that key granted
    the CRUD flag matching the request's HTTP method for that context
    (apps.api.models.ApiKeyPermission) - this is the entire authorization
    model for the API: no Django auth permissions, no groups, just
    "does this specific key have this specific verb on this specific
    context", checked fresh on every request.
    """

    def has_permission(self, request, view):
        api_key = getattr(request, "api_key", None)
        if api_key is None:
            return False

        context = getattr(view, "api_context", None)
        crud_field = _METHOD_TO_CRUD_FIELD.get(request.method)
        if context is None or crud_field is None:
            return False

        return api_key.permissions.filter(context=context, **{crud_field: True}).exists()


def has_context_permission(request, context, crud_field="can_read"):
    """The same check as HasContextPermission, for one more context - an
    endpoint whose data spans two contexts (e.g. `sessions/<id>/cooldown/`,
    a workout's stretches) needs the key to be allowed both."""
    api_key = getattr(request, "api_key", None)
    return api_key is not None and api_key.permissions.filter(
        context=context, **{crud_field: True}
    ).exists()
