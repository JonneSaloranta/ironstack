from django.urls import path

from . import views

app_name = "exercises"

urlpatterns = [
    path("", views.ExerciseListView.as_view(), name="exercise-list"),
    path("new/", views.ExerciseCreateView.as_view(), name="exercise-create"),
    path("<int:pk>/", views.ExerciseDetailView.as_view(), name="exercise-detail"),
    path("<int:pk>/edit/", views.ExerciseUpdateView.as_view(), name="exercise-update"),
    path(
        "<int:pk>/deactivate/",
        views.exercise_deactivate,
        name="exercise-deactivate",
    ),
    path(
        "<int:pk>/images/add/",
        views.exercise_image_create,
        name="exercise-image-create",
    ),
    path(
        "images/<int:pk>/delete/",
        views.exercise_image_delete,
        name="exercise-image-delete",
    ),
]
