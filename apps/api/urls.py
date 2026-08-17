from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

app_name = "api"

router = DefaultRouter()
router.register("exercises", views.ExerciseViewSet, basename="exercise")
router.register("muscle-groups", views.MuscleGroupViewSet, basename="muscle-group")
router.register("equipment", views.EquipmentViewSet, basename="equipment")
router.register("programs", views.ProgramViewSet, basename="program")
router.register("workouts", views.WorkoutViewSet, basename="workout")
router.register("prescriptions", views.ExercisePrescriptionViewSet, basename="prescription")
router.register("sessions", views.WorkoutSessionViewSet, basename="session")
router.register(
    "performed-exercises", views.PerformedExerciseViewSet, basename="performed-exercise"
)
router.register("sets", views.ExerciseSetViewSet, basename="set")
router.register("measurement-types", views.MeasurementTypeViewSet, basename="measurement-type")
router.register("measurements", views.BodyMeasurementViewSet, basename="measurement")
router.register("activity-types", views.ActivityTypeViewSet, basename="activity-type")
router.register("activities", views.ActivityViewSet, basename="activity")
router.register("records", views.PersonalRecordViewSet, basename="record")
router.register("foods", views.FoodViewSet, basename="food")
router.register("meal-slots", views.MealSlotViewSet, basename="meal-slot")
router.register("recipes", views.RecipeViewSet, basename="recipe")
router.register(
    "recipe-ingredients", views.RecipeIngredientViewSet, basename="recipe-ingredient"
)
router.register("diary-entries", views.DiaryEntryViewSet, basename="diary-entry")
router.register("nutrition-goals", views.NutritionGoalViewSet, basename="nutrition-goal")
router.register("nutrition-targets", views.NutritionTargetViewSet, basename="nutrition-target")

urlpatterns = [
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("analytics/summary/", views.TrainingSummaryView.as_view(), name="analytics-summary"),
    path(
        "analytics/achievements/",
        views.AchievementsView.as_view(),
        name="analytics-achievements",
    ),
] + router.urls
