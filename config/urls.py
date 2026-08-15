from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import RateLimitedLoginView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Must come before django.contrib.auth.urls' own "login/" below —
    # Django resolves urlpatterns in order and both mount at
    # "accounts/", so this is what actually serves /accounts/login/,
    # under the same "login" name every existing {% url %}/reverse()
    # call already uses (see apps.accounts.views.RateLimitedLoginView).
    path("accounts/login/", RateLimitedLoginView.as_view(), name="login"),
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
