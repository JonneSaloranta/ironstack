"""drf-spectacular integration (docs/API.md "Interactive docs").

Registering an `OpenApiAuthenticationExtension` for
`apps.api.auth.ApiKeyAuthentication` is the one piece drf-spectacular
can't infer on its own from `REST_FRAMEWORK["DEFAULT_AUTHENTICATION_
CLASSES"]` alone — without it, every generated operation would either
show no security requirement at all, or drf-spectacular would guess
wrong. This is what makes Swagger UI's "Authorize" button understand
the `Authorization: Bearer <key>` scheme apps.api.auth.
ApiKeyAuthentication actually expects, so a pasted-in key gets attached
to every "Try it out" request from then on.

Imported from ApiConfig.ready() (apps/api/apps.py) purely for the
import side effect — drf-spectacular discovers extensions through its
own global registry, populated whenever a subclass of one of its
extension base classes is defined/imported, not through a setting
naming this module explicitly.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.api.auth.ApiKeyAuthentication"
    name = "ApiKeyAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "An API key created from Profile → API keys "
                "(apps.api.models.ApiKey), sent as this exact header. "
                "The raw secret is only ever shown once, at creation."
            ),
        }
