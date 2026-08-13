from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.AnalyticsDashboardView.as_view(), name="dashboard"),
    path(
        "exercises/<int:pk>/",
        views.ExerciseAnalyticsView.as_view(),
        name="exercise",
    ),
]
