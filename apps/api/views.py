"""DRF views for every apps.api context — see docs/API.md.

Every viewset here goes through the exact same domain services the
server-rendered web views already use (apps.exercises.services,
apps.programs.services, apps.workouts.services, apps.records.services,
...) rather than re-deriving ownership/visibility rules or
snapshot-on-start/PR-detection side effects a second time — per
CLAUDE.md, business logic belongs in services, and a DRF view is exactly
as much a "view" as a Django one in that sense.
"""

from django.shortcuts import get_object_or_404
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
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
from apps.nutrition import diet_builder
from apps.nutrition import services as nutrition_services
from apps.nutrition.models import (
    DiaryEntry,
    DietPlan,
    DietPlanItem,
    DietPlanMeal,
    Food,
    NutritionGoal,
    NutritionProfile,
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
    DietPlanItemSerializer,
    DietPlanMealSerializer,
    DietPlanSerializer,
    EquipmentSerializer,
    ExercisePrescriptionSerializer,
    ExerciseSerializer,
    ExerciseSetSerializer,
    FoodSerializer,
    MealSlotSerializer,
    MeasurementTypeSerializer,
    MuscleGroupSerializer,
    NutritionGoalSerializer,
    NutritionProfileSerializer,
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


class NutritionProfileView(generics.RetrieveUpdateAPIView):
    """A singleton resource, the same shape as ProfileView above —
    GET/PATCH always act on the authenticated key's own
    NutritionProfile. get_object_or_404 rather than a bare attribute
    lookup: a user who hasn't gone through nutrition onboarding yet
    has no NutritionProfile row at all, and that's a plain 404, not a
    500."""

    serializer_class = NutritionProfileSerializer
    api_context = ApiContext.NUTRITION

    def get_object(self):
        return get_object_or_404(NutritionProfile, user=self.request.user)


class DietPlanViewSet(viewsets.ModelViewSet):
    """create/activate/deactivate/apply all go through
    apps.nutrition.diet_builder/services rather than a raw field
    write — see DietPlanSerializer's own docstring for why."""

    serializer_class = DietPlanSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return DietPlan.objects.filter(user=self.request.user).prefetch_related(
            "meals__meal_slot", "meals__items"
        )

    def perform_create(self, serializer):
        data = serializer.validated_data
        plan = diet_builder.build_diet_plan(
            self.request.user,
            name=data["name"],
            goal=data.get("goal"),
            target_calories=data["target_calories"],
            target_protein_grams=data["target_protein_grams"],
            target_carbohydrate_grams=data["target_carbohydrate_grams"],
            target_fat_grams=data["target_fat_grams"],
            meal_slots=data["meal_slots"],
            is_weekly=data.get("is_weekly", False),
        )
        serializer.instance = plan

    # A plan's target_* fields must stay writable input on the
    # serializer (perform_create passes them straight to
    # build_diet_plan, and Meta.read_only_fields would strip them from
    # validated_data on create too, not just update) — so "read-only
    # after creation" is enforced here instead, by discarding them
    # before the default ModelSerializer.update() gets a chance to
    # write them onto the instance.
    _UPDATE_ONLY_DISCARDED_FIELDS = (
        "meal_slots",
        "target_calories",
        "target_protein_grams",
        "target_carbohydrate_grams",
        "target_fat_grams",
    )

    def perform_update(self, serializer):
        for field in self._UPDATE_ONLY_DISCARDED_FIELDS:
            serializer.validated_data.pop(field, None)
        serializer.save()

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        plan = self.get_object()
        nutrition_services.set_active_diet_plan(request.user, plan)
        return Response(self.get_serializer(plan).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        plan = self.get_object()
        nutrition_services.deactivate_diet_plan(plan)
        return Response(self.get_serializer(plan).data)

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        """Materializes this plan into real DiaryEntry rows for
        `date` — the one thing an API consumer would otherwise have
        to reconstruct by hand (fetch every DietPlanItem, POST N
        diary-entries/ individually)."""
        from datetime import date as date_cls

        plan = self.get_object()
        raw_date = request.data.get("date")
        if not raw_date:
            return Response(
                {"date": "This field is required."}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            target_date = date_cls.fromisoformat(raw_date)
        except ValueError:
            return Response(
                {"date": "Must be in YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST
            )
        entries = diet_builder.apply_diet_plan(plan, target_date)
        return Response(
            DiaryEntrySerializer(entries, many=True).data, status=status.HTTP_201_CREATED
        )


class DietPlanMealViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only — see DietPlanSerializer's own docstring for why."""

    serializer_class = DietPlanMealSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return DietPlanMeal.objects.filter(diet_plan__user=self.request.user)


class DietPlanItemViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only — same reasoning as DietPlanMealViewSet above."""

    serializer_class = DietPlanItemSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return DietPlanItem.objects.filter(diet_plan_meal__diet_plan__user=self.request.user)
