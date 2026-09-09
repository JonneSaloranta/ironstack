"""Interactive API docs (docs/API.md "Interactive docs") — the
human-facing, session-authenticated counterpart to apps.api's actual
API, same reasoning as apps.api.urls_web's own docstring: the schema/
Swagger UI views themselves need `login_required` (this is a private,
self-hosted instance's own data model, not something to expose to
anyone who finds the URL), even though the "Try it out" requests they
send from the browser still authenticate against the real API the
normal way (Authorization: Bearer <key>, apps.api.auth.
ApiKeyAuthentication) — logging into the web app and holding an API
key are two separate credentials, and this only ever asks for the
first one.
"""

from django.contrib.auth.decorators import login_required
from django.urls import path
from drf_spectacular.views import SpectacularAPIView

from .views_docs import IronStackSwaggerView, SwaggerUIBootstrapScriptView

app_name = "api_docs"

urlpatterns = [
    path("schema/", login_required(SpectacularAPIView.as_view()), name="schema"),
    path(
        "",
        login_required(IronStackSwaggerView.as_view(url_name="api_docs:schema")),
        name="swagger-ui",
    ),
    # apps.api.views_docs' own docstring — a same-origin <script src>
    # for the bootstrap script IronStackSwaggerView's page loads,
    # instead of drf-spectacular's own default inline <script> block
    # this app's CSP doesn't allow.
    path(
        "swagger-ui-bootstrap.js",
        login_required(SwaggerUIBootstrapScriptView.as_view(url_name="api_docs:schema")),
        name="swagger-ui-bootstrap",
    ),
]
