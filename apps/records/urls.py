from django.urls import path

from . import views

app_name = "records"

urlpatterns = [
    path(
        "exercises/<int:exercise_pk>/",
        views.ExerciseRecordsView.as_view(),
        name="exercise-records",
    ),
]
