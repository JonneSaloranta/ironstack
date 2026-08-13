from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("exercises/", include("apps.exercises.urls")),
    path("programs/", include("apps.programs.urls")),
    path("workouts/", include("apps.workouts.urls")),
    path("records/", include("apps.records.urls")),
    path("measurements/", include("apps.measurements.urls")),
    path("activities/", include("apps.activities.urls")),
    path("", include("apps.core.urls")),
]
