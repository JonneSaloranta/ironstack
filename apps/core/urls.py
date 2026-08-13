from django.urls import path

from .views import DashboardView, healthcheck

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("healthz/", healthcheck, name="healthcheck"),
]
