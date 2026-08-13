from django.urls import path

from . import views

app_name = "activities"

urlpatterns = [
    path("", views.ActivityTypeListView.as_view(), name="type-list"),
    path("new/", views.ActivityTypeCreateView.as_view(), name="type-create"),
    path(
        "<int:pk>/deactivate/",
        views.activity_type_deactivate,
        name="type-deactivate",
    ),
    path("<int:pk>/", views.ActivityHistoryView.as_view(), name="history"),
    path("<int:type_pk>/log/", views.activity_log, name="log"),
    path("entries/<int:pk>/edit/", views.activity_edit, name="entry-edit"),
    path("entries/<int:pk>/delete/", views.activity_delete, name="entry-delete"),
]
