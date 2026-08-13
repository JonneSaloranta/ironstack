from django.urls import path

from . import views

app_name = "programs"

urlpatterns = [
    path("", views.ProgramListView.as_view(), name="program-list"),
    path("new/", views.ProgramCreateView.as_view(), name="program-create"),
    path("<int:pk>/", views.ProgramDetailView.as_view(), name="program-detail"),
    path("<int:pk>/edit/", views.ProgramUpdateView.as_view(), name="program-update"),
    path("<int:pk>/delete/", views.ProgramDeleteView.as_view(), name="program-delete"),
    path("<int:pk>/copy/", views.program_copy, name="program-copy"),
    path(
        "<int:program_pk>/workouts/new/",
        views.workout_create,
        name="workout-create",
    ),
    path(
        "<int:program_pk>/workouts/<int:pk>/edit/",
        views.workout_update,
        name="workout-update",
    ),
    path(
        "<int:program_pk>/workouts/<int:pk>/delete/",
        views.workout_delete,
        name="workout-delete",
    ),
    path(
        "<int:program_pk>/workouts/<int:workout_pk>/prescriptions/new/",
        views.prescription_create,
        name="prescription-create",
    ),
    path(
        "<int:program_pk>/workouts/<int:workout_pk>/prescriptions/<int:pk>/edit/",
        views.prescription_update,
        name="prescription-update",
    ),
    path(
        "<int:program_pk>/workouts/<int:workout_pk>/prescriptions/<int:pk>/delete/",
        views.prescription_delete,
        name="prescription-delete",
    ),
]
