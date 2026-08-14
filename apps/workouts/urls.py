from django.urls import path

from . import views

app_name = "workouts"

urlpatterns = [
    path("", views.WorkoutSessionListView.as_view(), name="session-list"),
    path("start/", views.session_start_freeform, name="session-start-freeform"),
    path(
        "start/<int:workout_pk>/", views.session_start, name="session-start"
    ),
    path("<int:pk>/", views.WorkoutSessionDetailView.as_view(), name="session-detail"),
    path("<int:pk>/train/", views.session_train, name="session-train"),
    path("<int:pk>/complete/", views.session_complete, name="session-complete"),
    path("<int:pk>/abandon/", views.session_abandon, name="session-abandon"),
    path("<int:pk>/delete/", views.session_delete, name="session-delete"),
    path(
        "<int:session_pk>/exercises/add/",
        views.performed_exercise_add,
        name="performed-exercise-add",
    ),
    path(
        "exercises/<int:performed_exercise_pk>/sets/log/",
        views.set_log,
        name="set-log",
    ),
    path(
        "exercises/<int:performed_exercise_pk>/sets/train-log/",
        views.train_set_log,
        name="train-set-log",
    ),
    path("sets/<int:pk>/edit/", views.set_edit, name="set-edit"),
    path("sets/<int:pk>/delete/", views.set_delete, name="set-delete"),
]
