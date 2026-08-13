from django.urls import path

from . import views

app_name = "measurements"

urlpatterns = [
    path("", views.MeasurementTypeListView.as_view(), name="type-list"),
    path("new/", views.MeasurementTypeCreateView.as_view(), name="type-create"),
    path(
        "<int:pk>/deactivate/",
        views.measurement_type_deactivate,
        name="type-deactivate",
    ),
    path("<int:pk>/", views.MeasurementHistoryView.as_view(), name="history"),
    path("<int:type_pk>/log/", views.measurement_log, name="log"),
    path("entries/<int:pk>/edit/", views.measurement_edit, name="entry-edit"),
    path("entries/<int:pk>/delete/", views.measurement_delete, name="entry-delete"),
]
