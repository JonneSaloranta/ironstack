from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import RateLimitedLoginView, RateLimitedPasswordResetView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Must come before django.contrib.auth.urls' own "login/"/
    # "password_reset/" below — Django resolves urlpatterns in order
    # and both mount at "accounts/", so these are what actually serve
    # those two URLs, under the same "login"/"password_reset" names
    # every existing {% url %}/reverse() call already uses (see
    # apps.accounts.views.RateLimitedLoginView/
    # RateLimitedPasswordResetView).
    path("accounts/login/", RateLimitedLoginView.as_view(), name="login"),
    path(
        "accounts/password_reset/",
        RateLimitedPasswordResetView.as_view(),
        name="password_reset",
    ),
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("exercises/", include("apps.exercises.urls")),
    path("programs/", include("apps.programs.urls")),
    path("workouts/", include("apps.workouts.urls")),
    path("records/", include("apps.records.urls")),
    path("measurements/", include("apps.measurements.urls")),
    path("activities/", include("apps.activities.urls")),
    path("analytics/", include("apps.analytics.urls")),
    path("api/v1/", include("apps.api.urls")),
    path("api/keys/", include("apps.api.urls_web")),
    path("", include("apps.core.urls")),
]

if settings.AUTHENTIK_ENABLED:
    # "oidc/authenticate/", "oidc/callback/", "oidc/logout/" (see
    # mozilla_django_oidc.urls) — gated the same way settings.
    # AUTHENTICATION_BACKENDS/OIDC_* config.settings.base is, so a
    # request never reaches a view that would fail with a confusing
    # error against unconfigured OIDC_OP_* endpoints. The callback
    # path is what AUTHENTIK_CLIENT_ID's redirect URI in Authentik
    # must point at: f"{this app's base URL}/oidc/callback/".
    urlpatterns += [path("oidc/", include("mozilla_django_oidc.urls"))]
