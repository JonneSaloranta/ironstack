"""DRF views for every apps.api context — see docs/API.md.

Every viewset here goes through the exact same domain services the
server-rendered web views already use (apps.exercises.services,
apps.programs.services, apps.workouts.services, apps.records.services,
...) rather than re-deriving ownership/visibility rules or
snapshot-on-start/PR-detection side effects a second time — per
CLAUDE.md, business logic belongs in services, and a DRF view is exactly
as much a "view" as a Django one in that sense.
"""

from rest_framework import generics, mixins, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.activities import services as activity_services
from apps.activities.models import Activity
from apps.analytics import achievements as achievement_services
from apps.analytics import dateranges
from apps.analytics import services as analytics_services
from apps.exercises import services as exercise_services
from apps.exercises.models import Equipment, MuscleGroup
from apps.measurements import services as measurement_services
from apps.measurements.models import BodyMeasurement
from apps.nutrition import services as nutrition_services
from apps.nutrition.models import (
    DiaryEntry,
    Food,
    NutritionGoal,
    NutritionTarget,
    Recipe,
    RecipeIngredient,
)
from apps.programs import services as program_services
from apps.programs.models import ExercisePrescription, Workout
from apps.records import services as records_services
from apps.records.models import PersonalRecord
from apps.workouts import services as workout_services
from apps.workouts.models import ExerciseSet, PerformedExercise

from .models import ApiContext
from .serializers import (
    AchievementSerializer,
    ActivitySerializer,
    ActivityTypeSerializer,
    BodyMeasurementSerializer,
    DiaryEntrySerializer,
    EquipmentSerializer,
    ExercisePrescriptionSerializer,
    ExerciseSerializer,
    ExerciseSetSerializer,
    FoodSerializer,
    MealSlotSerializer,
    MeasurementTypeSerializer,
    MuscleGroupSerializer,
    NutritionGoalSerializer,
    NutritionTargetSerializer,
    PerformedExerciseSerializer,
    PersonalRecordSerializer,
    ProfileSerializer,
    ProgramSerializer,
    RecipeIngredientSerializer,
    RecipeSerializer,
    TrainingSummarySerializer,
    WorkoutSerializer,
    WorkoutSessionSerializer,
)
from .viewsets import OwnedResourceViewSet

# --------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------


class ProfileView(generics.RetrieveUpdateAPIView):
    """A singleton resource — GET/PATCH always act on the authenticated
    key's own user, never a list or an id in the URL. Create/delete
    aren't meaningful here (an account isn't made or removed through
    this API); the profile context's can_create/can_delete permission
    flags simply have no route to authorize."""

    serializer_class = ProfileSerializer
    api_context = ApiContext.PROFILE

    def get_object(self):
        return self.request.user


# --------------------------------------------------------------------
# Exercises
# --------------------------------------------------------------------


class MuscleGroupViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Reference data (system-seeded, never user-created) — read-only,
    grouped under the exercises context since that's the only place it's
    ever used from."""

    queryset = MuscleGroup.objects.all()
    serializer_class = MuscleGroupSerializer
    api_context = ApiContext.EXERCISES


class EquipmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Equipment.objects.all()
    serializer_class = EquipmentSerializer
    api_context = ApiContext.EXERCISES


class ExerciseViewSet(OwnedResourceViewSet):
    serializer_class = ExerciseSerializer
    api_context = ApiContext.EXERCISES

    def visible_queryset(self):
        return exercise_services.visible_to(self.request.user)


# --------------------------------------------------------------------
# Programs (Program / Workout / ExercisePrescription)
# --------------------------------------------------------------------


class ProgramViewSet(OwnedResourceViewSet):
    serializer_class = ProgramSerializer
    api_context = ApiContext.PROGRAMS
    soft_delete = False  # Program has no `active` field — matches its web view's real delete

    def visible_queryset(self):
        return program_services.visible_to(self.request.user)

    def editable_queryset(self):
        return program_services.editable_by(self.request.user)


class WorkoutViewSet(viewsets.ModelViewSet):
    serializer_class = WorkoutSerializer
    api_context = ApiContext.PROGRAMS

    def get_queryset(self):
        if self.action in ("update", "partial_update", "destroy"):
            programs = program_services.editable_by(self.request.user)
        else:
            programs = program_services.visible_to(self.request.user)
        return Workout.objects.filter(program__in=programs)


class ExercisePrescriptionViewSet(viewsets.ModelViewSet):
    serializer_class = ExercisePrescriptionSerializer
    api_context = ApiContext.PROGRAMS

    def get_queryset(self):
        if self.action in ("update", "partial_update", "destroy"):
            programs = program_services.editable_by(self.request.user)
        else:
            programs = program_services.visible_to(self.request.user)
        return ExercisePrescription.objects.filter(workout__program__in=programs)


# --------------------------------------------------------------------
# Workouts (session logging)
# --------------------------------------------------------------------


class WorkoutSessionViewSet(viewsets.ModelViewSet):
    serializer_class = WorkoutSessionSerializer
    api_context = ApiContext.WORKOUTS
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return workout_services.sessions_for(self.request.user).prefetch_related(
            "performed_exercises__sets"
        )

    def perform_create(self, serializer):
        workout = serializer.validated_data.get("workout")
        serializer.instance = workout_services.start_session(self.request.user, workout=workout)

    def perform_update(self, serializer):
        session = serializer.instance
        new_status = serializer.validated_data.get("status")
        if new_status == "completed":
            workout_services.complete_session(session)
        elif new_status == "abandoned":
            workout_services.abandon_session(session)

    def perform_destroy(self, instance):
        instance.delete()


class PerformedExerciseViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Create + read only — the web UI has no way to edit or remove a
    performed exercise mid-session either, only add one
    (apps.workouts.views.performed_exercise_add)."""

    serializer_class = PerformedExerciseSerializer
    api_context = ApiContext.WORKOUTS

    def get_queryset(self):
        return PerformedExercise.objects.filter(session__user=self.request.user)

    def perform_create(self, serializer):
        session = serializer.validated_data["session"]
        exercise = serializer.validated_data["exercise"]
        serializer.instance = workout_services.add_performed_exercise(session, exercise)


class ExerciseSetViewSet(viewsets.ModelViewSet):
    """The actual "log a set" endpoint — perform_create goes through
    apps.workouts.services.log_set (auto-numbers set_number, ignoring
    anything a caller sent for it) and then
    apps.records.services.check_and_record_prs, exactly the two calls
    apps.workouts.views.set_log makes for the same action on the web
    side, so a set logged via the API sets PRs the same as one logged
    through the UI."""

    serializer_class = ExerciseSetSerializer
    api_context = ApiContext.WORKOUTS

    def get_queryset(self):
        return ExerciseSet.objects.filter(performed_exercise__session__user=self.request.user)

    def perform_create(self, serializer):
        performed_exercise = serializer.validated_data["performed_exercise"]
        fields = {
            key: value
            for key, value in serializer.validated_data.items()
            if key != "performed_exercise"
        }
        logged_set = workout_services.log_set(performed_exercise, **fields)
        records_services.check_and_record_prs(logged_set)
        serializer.instance = logged_set


# --------------------------------------------------------------------
# Measurements
# --------------------------------------------------------------------


class MeasurementTypeViewSet(OwnedResourceViewSet):
    serializer_class = MeasurementTypeSerializer
    api_context = ApiContext.MEASUREMENTS

    def visible_queryset(self):
        return measurement_services.visible_to(self.request.user)


class BodyMeasurementViewSet(viewsets.ModelViewSet):
    serializer_class = BodyMeasurementSerializer
    api_context = ApiContext.MEASUREMENTS

    def get_queryset(self):
        return BodyMeasurement.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# --------------------------------------------------------------------
# Activities
# --------------------------------------------------------------------


class ActivityTypeViewSet(OwnedResourceViewSet):
    serializer_class = ActivityTypeSerializer
    api_context = ApiContext.ACTIVITIES

    def visible_queryset(self):
        return activity_services.visible_to(self.request.user)


class ActivityViewSet(viewsets.ModelViewSet):
    serializer_class = ActivitySerializer
    api_context = ApiContext.ACTIVITIES

    def get_queryset(self):
        return Activity.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# --------------------------------------------------------------------
# Records (read-only — PRs are derived, never directly writable)
# --------------------------------------------------------------------


class PersonalRecordViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = PersonalRecordSerializer
    api_context = ApiContext.RECORDS

    def get_queryset(self):
        return PersonalRecord.objects.filter(user=self.request.user).select_related("exercise")


# --------------------------------------------------------------------
# Analytics (read-only aggregates — no backing model of their own)
# --------------------------------------------------------------------


class TrainingSummaryView(APIView):
    """`?range=7d|30d|all` (default 30d, matching
    apps.analytics.views.AnalyticsDashboardView) — total sessions/
    duration/volume in canonical kg (see apps.api.serializers' own
    docstring for why this API never converts to a display unit)."""

    api_context = ApiContext.ANALYTICS

    def get(self, request):
        date_range = dateranges.resolve(request.query_params.get("range", "30d"))
        summary = analytics_services.training_summary_canonical(request.user, date_range)
        return Response(TrainingSummarySerializer(summary).data)


class AchievementsView(APIView):
    """The same all-time, shared-across-users highlights the dashboard
    carousel shows (apps.analytics.achievements) — see
    docs/UI.md "Achievements carousel" for what show_achievements does
    to this list."""

    api_context = ApiContext.ANALYTICS

    def get(self, request):
        highlights = achievement_services.achievement_highlights()
        return Response(AchievementSerializer(highlights, many=True).data)


# --------------------------------------------------------------------
# Nutrition
# --------------------------------------------------------------------


class FoodViewSet(OwnedResourceViewSet):
    serializer_class = FoodSerializer
    api_context = ApiContext.NUTRITION

    def visible_queryset(self):
        from django.db.models import Q

        return Food.objects.filter(Q(owner=self.request.user) | Q(owner__isnull=True))


class MealSlotViewSet(OwnedResourceViewSet):
    serializer_class = MealSlotSerializer
    api_context = ApiContext.NUTRITION

    def visible_queryset(self):
        return nutrition_services.visible_meal_slots(self.request.user)


class RecipeViewSet(viewsets.ModelViewSet):
    serializer_class = RecipeSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        # RecipeSerializer nests every ingredient (with its food) —
        # prefetched here so listing many recipes stays two queries
        # total (recipes + ingredients), not one ingredients query per
        # recipe in the list.
        return Recipe.objects.filter(owner=self.request.user).prefetch_related(
            "ingredients__food"
        )

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class RecipeIngredientViewSet(viewsets.ModelViewSet):
    serializer_class = RecipeIngredientSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return RecipeIngredient.objects.filter(recipe__owner=self.request.user)


class DiaryEntryViewSet(viewsets.ModelViewSet):
    serializer_class = DiaryEntrySerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return DiaryEntry.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class NutritionGoalViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only — see NutritionGoalSerializer's own docstring for why:
    a goal is only ever created/superseded through
    apps.nutrition.services.set_goal, never a raw PATCH."""

    serializer_class = NutritionGoalSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return NutritionGoal.objects.filter(user=self.request.user)


class NutritionTargetViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only — same reasoning as NutritionGoalViewSet above."""

    serializer_class = NutritionTargetSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return NutritionTarget.objects.filter(user=self.request.user)
