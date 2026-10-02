from django.urls import path

from . import views

app_name = "tutorials"

urlpatterns = [
    path("", views.TutorialListView.as_view(), name="list"),
    path("settings/", views.tutorial_settings, name="settings"),
    path("disable/", views.disable, name="disable"),
    path("reset/", views.reset_all, name="reset-all"),
    path("<slug:key>/complete/", views.complete, name="complete"),
    path("<slug:key>/reset/", views.reset_one, name="reset"),
]
