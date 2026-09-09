"""Swagger UI, adapted to this app's CSP (docs/API.md "Interactive
docs"). drf_spectacular.views.SpectacularSwaggerView's own template
bootstraps the UI with an *inline* `<script>...</script>` block —
fine for a default install, but this app's CSP has no script-src
'unsafe-inline' allowance at all (apps.core.middleware.
ContentSecurityPolicyMiddleware, docs/SECURITY.md
"Content-Security-Policy"), and isn't getting one just for this one
page. drf-spectacular's own template already anticipates exactly this
case though: it renders that inline block only when a `script_url`
context value is empty, and uses `<script src="{{ script_url }}">`
instead when one is supplied — the two views below are what supplies
it, splitting the same context the base view already builds
(`settings`, `schema_auth_names`, `schema_url`, ...) across an HTML
page that references a plain same-origin script, and a second,
same-origin endpoint serving that script's actual (still per-request,
since it embeds schema_auth_names/CSRF header name) content.
"""

from django.template.loader import render_to_string
from django.urls import reverse
from drf_spectacular.views import SpectacularSwaggerView
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response


class _JavaScriptRenderer(BaseRenderer):
    media_type = "application/javascript"
    format = "js"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class IronStackSwaggerView(SpectacularSwaggerView):
    """The actual `/api/docs/` page — identical to the base view's own
    HTML except for pointing script_url at SwaggerUIBootstrapScriptView
    below instead of leaving it empty."""

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        response.data["script_url"] = reverse("api_docs:swagger-ui-bootstrap")
        return response


class SwaggerUIBootstrapScriptView(SpectacularSwaggerView):
    """Renders `drf_spectacular/swagger_ui.js` — the exact same
    template the base view would otherwise inline directly into the
    HTML page — as its own same-origin response instead, so the page
    can load it via a plain `<script src>` (allowed under script-src
    'self') rather than needing 'unsafe-inline'."""

    renderer_classes = [_JavaScriptRenderer]

    def get(self, request, *args, **kwargs):
        # Reuses the base view's own get() purely to build the same
        # context dict (settings/schema_url/csrf_header_name/
        # schema_auth_names/...) it would otherwise hand to the HTML
        # template — cheap (no I/O beyond what serving the page itself
        # already does) and guarantees the two responses can never
        # drift out of sync with each other.
        html_response = super().get(request, *args, **kwargs)
        script = render_to_string(
            html_response.data["template_name_js"], html_response.data, request
        )
        return Response(script, content_type="application/javascript")
