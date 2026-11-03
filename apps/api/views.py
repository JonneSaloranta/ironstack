"""DRF views for every apps.api context - see docs/API.md.

Every viewset here goes through the exact same domain services the
server-rendered web views already use (apps.exercises.services,
apps.programs.services, apps.workouts.services, apps.records.services,
...) rather than re-deriving ownership/visibility rules or
snapshot-on-start/PR-detection side effects a second time - per
CLAUDE.md, business logic belongs in services, and a DRF view is exactly
as much a "view" as a Django one in that sense.
"""

from contextlib import nullcontext
from datetime import datetime, time

from django.shortcuts import get_object_or_404
from django.utils import timezone, translation
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, mixins, serializers, status, viewsets
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
from apps.stretching import services as stretching_services
from apps.stretching.models import RoutineItem
from apps.workouts import services as workout_services
from apps.workouts.models import ExerciseSet, PerformedExercise, WorkoutSessionStatus

from . import idempotency
from .idempotency import IdempotentCreateMixin
from .models import ApiContext
from .serializers import (
    AchievementSerializer,
    ActivitySerializer,
    ActivityTypeSerializer,
    BodyMeasurementSerializer,
    DailyTotalsSerializer,
    DiaryCopyDayResultSerializer,
    DiaryCopyDaySerializer,
    DiaryEntrySerializer,
    DietPlanItemSerializer,
    DietPlanMealSerializer,
    DietPlanSerializer,
    EquipmentSerializer,
    ExercisePrescriptionSerializer,
    ExerciseSerializer,
    ExerciseSetCreatedSerializer,
    ExerciseSetSerializer,
    FoodSerializer,
    LastPerformanceSerializer,
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
    RoutineItemSerializer,
    ShoppingListSerializer,
    ShoppingSettingsSerializer,
    StretchRoutineSerializer,
    StretchSerializer,
    StretchSessionSerializer,
    SuggestionSerializer,
    TrainingSummarySerializer,
    UpNextSerializer,
    WorkoutSerializer,
    WorkoutSessionSerializer,
)
from .viewsets import OwnedResourceViewSet

# --------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------


class ProfileView(generics.RetrieveUpdateAPIView):
    """A singleton resource - GET/PATCH always act on the authenticated
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
    """Reference data (system-seeded, never user-created) - read-only,
    grouped under the exercises context since that's the only place it's
    ever used from."""

    queryset = MuscleGroup.objects.all()
    serializer_class = MuscleGroupSerializer
    api_context = ApiContext.EXERCISES


class EquipmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Equipment.objects.all()
    serializer_class = EquipmentSerializer
    api_context = ApiContext.EXERCISES


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                "search",
                description="Matches the name, also as translated to your language.",
            ),
            OpenApiParameter("ids", description="Comma-separated ids, e.g. 1,2,3."),
        ]
    )
)
class ExerciseViewSet(OwnedResourceViewSet):
    """`?search=` and `?ids=` narrow the list (issue #18) - an exercise
    picker finds what it needs without paging through the whole
    library."""

    serializer_class = ExerciseSerializer
    api_context = ApiContext.EXERCISES

    def visible_queryset(self):
        return exercise_services.visible_to(self.request.user)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != "list":
            return queryset
        queryset = _filter_by_ids(self.request, queryset)
        query = self.request.query_params.get("search")
        if query:
            # An API key's request never passes through
            # UserLanguageMiddleware with its user (DRF authenticates
            # later), so the user's language is applied here - search
            # matches the translated names they see in the app.
            language = self.request.user.language
            with translation.override(language) if language else nullcontext():
                queryset = exercise_services.search(queryset, query)
        return queryset


# --------------------------------------------------------------------
# Programs (Program / Workout / ExercisePrescription)
# --------------------------------------------------------------------


class ProgramViewSet(OwnedResourceViewSet):
    serializer_class = ProgramSerializer
    api_context = ApiContext.PROGRAMS
    soft_delete = False  # Program has no `active` field - matches its web view's real delete

    def visible_queryset(self):
        return program_services.visible_to(self.request.user).prefetch_related(
            "workouts__prescriptions__exercise"
        )

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
        return Workout.objects.filter(program__in=programs).prefetch_related(
            "prescriptions__exercise"
        )


class ExercisePrescriptionViewSet(viewsets.ModelViewSet):
    serializer_class = ExercisePrescriptionSerializer
    api_context = ApiContext.PROGRAMS

    def get_queryset(self):
        if self.action in ("update", "partial_update", "destroy"):
            programs = program_services.editable_by(self.request.user)
        else:
            programs = program_services.visible_to(self.request.user)
        return ExercisePrescription.objects.filter(workout__program__in=programs).select_related(
            "exercise"
        )


# --------------------------------------------------------------------
# Workouts (session logging)
# --------------------------------------------------------------------


def _query_datetime(request, name):
    """`?<name>=` as an aware datetime: ISO 8601 (`2026-10-01T18:00:00Z`)
    or a bare date, meaning its midnight in the server's time zone. None
    when absent; a 400 when present but unparseable."""
    raw = request.query_params.get(name)
    if raw is None:
        return None
    try:
        value = parse_datetime(raw)
        if value is None:
            day = parse_date(raw)
            if day is not None:
                value = datetime.combine(day, time.min)
    except ValueError:  # well-formed but impossible, e.g. month 13
        value = None
    if value is None:
        raise serializers.ValidationError({name: "Must be an ISO 8601 date or datetime."})
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value


def _query_date(request, name):
    """`?<name>=YYYY-MM-DD` as a date; None when absent, a 400 when
    present but unparseable."""
    raw = request.query_params.get(name)
    if raw is None:
        return None
    try:
        value = parse_date(raw)
    except ValueError:  # well-formed but impossible, e.g. month 13
        value = None
    if value is None:
        raise serializers.ValidationError({name: "Must be a date, YYYY-MM-DD."})
    return value


def _query_id(request, name):
    """`?<name>=<id>` as an int; None when absent, a 400 otherwise."""
    raw = request.query_params.get(name)
    if raw is None:
        return None
    if not raw.strip().isdigit():
        raise serializers.ValidationError({name: "Must be an id."})
    return int(raw)


def _filter_by_ids(request, queryset):
    """`?ids=1,2,3` narrows `queryset` to those ids (a 400 when malformed)."""
    raw_ids = request.query_params.get("ids")
    if raw_ids is None:
        return queryset
    parts = [part.strip() for part in raw_ids.split(",") if part.strip()]
    if not all(part.isdigit() for part in parts):
        raise serializers.ValidationError({"ids": "Must be comma-separated ids."})
    return queryset.filter(pk__in=[int(part) for part in parts])


def _filter_by_dates(request, queryset, field, *, after="date_after", before="date_before"):
    """`?date_after=` (inclusive) / `?date_before=` (exclusive) on a
    DateField - the same semantics as sessions/' started_after/
    started_before."""
    start = _query_date(request, after)
    if start is not None:
        queryset = queryset.filter(**{f"{field}__gte": start})
    end = _query_date(request, before)
    if end is not None:
        queryset = queryset.filter(**{f"{field}__lt": end})
    return queryset


def _date_range_parameters(after="date_after", before="date_before", *, what="date"):
    return [
        OpenApiParameter(after, description=f"Inclusive: {what} on or after this date."),
        OpenApiParameter(before, description=f"Exclusive: {what} before this date."),
    ]


@extend_schema_view(
    create=extend_schema(parameters=[idempotency.SCHEMA_PARAMETER]),
    list=extend_schema(
        parameters=[
            OpenApiParameter("status", enum=WorkoutSessionStatus.values),
            OpenApiParameter(
                "started_after", description="Inclusive: started at or after this date/time."
            ),
            OpenApiParameter(
                "started_before", description="Exclusive: started before this date/time."
            ),
        ]
    )
)
class WorkoutSessionViewSet(IdempotentCreateMixin, viewsets.ModelViewSet):
    """`?status=` and `?started_after=`/`?started_before=` narrow the
    list (issue #17) - a client resuming after a restart finds the
    session in progress with one request instead of paging through
    every session it ever logged."""

    serializer_class = WorkoutSessionSerializer
    api_context = ApiContext.WORKOUTS
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = workout_services.sessions_for(self.request.user).prefetch_related(
            "performed_exercises__sets"
        )
        if self.action != "list":
            return queryset
        session_status = self.request.query_params.get("status")
        if session_status is not None:
            if session_status not in WorkoutSessionStatus.values:
                raise serializers.ValidationError(
                    {"status": f"Must be one of: {', '.join(WorkoutSessionStatus.values)}."}
                )
            queryset = queryset.filter(status=session_status)
        started_after = _query_datetime(self.request, "started_after")
        if started_after is not None:
            queryset = queryset.filter(started_at__gte=started_after)
        started_before = _query_datetime(self.request, "started_before")
        if started_before is not None:
            queryset = queryset.filter(started_at__lt=started_before)
        return queryset

    def perform_create(self, serializer):
        workout = serializer.validated_data.get("workout")
        serializer.instance = workout_services.start_session(self.request.user, workout=workout)

    def perform_update(self, serializer):
        session = serializer.instance
        new_status = serializer.validated_data.get("status")
        if not session.is_in_progress:
            return
        if new_status == "completed":
            workout_services.complete_session(session)
        elif new_status == "abandoned":
            workout_services.abandon_session(session)

    def perform_destroy(self, instance):
        exercise_ids = list(instance.performed_exercises.values_list("exercise_id", flat=True))
        instance.delete()
        records_services.rebuild_records_for_exercises(self.request.user, exercise_ids)


@extend_schema_view(create=extend_schema(parameters=[idempotency.SCHEMA_PARAMETER]))
class PerformedExerciseViewSet(
    IdempotentCreateMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Create + read only - the web UI has no way to edit or remove a
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

    @extend_schema(responses=SuggestionSerializer)
    @action(detail=True, methods=["get"])
    def suggestion(self, request, pk=None):
        """The explainable weight suggestion the web shows when logging
        a set (apps.progression.suggestions.suggest_weight, issue #15).
        Only ever a default: nothing is logged until the client posts a
        set the user confirmed."""
        from apps.progression.suggestions import suggest_weight

        performed = self.get_object()
        empty = dict.fromkeys(SuggestionSerializer().fields)
        if performed.prescription_id is None:
            return Response(empty)
        # The reason text is translated as it's built; an API key's
        # request has no user language active yet (see ExerciseViewSet).
        language = request.user.language
        with translation.override(language) if language else nullcontext():
            suggestion = suggest_weight(request.user, performed.prescription)
            data = SuggestionSerializer(
                {
                    "suggested_weight": suggestion.suggested_weight,
                    "target_min_reps": suggestion.target_min_reps,
                    "target_max_reps": suggestion.target_max_reps,
                    "method": performed.prescription.progression_method,
                    "action": suggestion.action,
                    "confidence": suggestion.confidence,
                    "reason": suggestion.reason,
                }
            ).data
        return Response(data)


@extend_schema_view(
    create=extend_schema(
        parameters=[idempotency.SCHEMA_PARAMETER],
        request=ExerciseSetSerializer,
        responses={201: ExerciseSetCreatedSerializer},
    )
)
class ExerciseSetViewSet(IdempotentCreateMixin, viewsets.ModelViewSet):
    """The actual "log a set" endpoint - perform_create goes through
    apps.workouts.services.log_set (auto-numbers set_number, ignoring
    anything a caller sent for it) and then
    apps.records.services.check_and_record_prs, exactly the two calls
    apps.workouts.views.set_log makes for the same action on the web
    side, so a set logged via the API sets PRs the same as one logged
    through the UI. Editing or deleting a set rebuilds that exercise's
    records (apps.records.services.rebuild_records), same as the web's
    set_edit/set_delete."""

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
        # The response lists the records this set broke (issue #30) -
        # the web's PR toast - so a client needn't diff records/.
        serializer.context["new_records"] = records_services.check_and_record_prs(logged_set)
        serializer.instance = logged_set

    def perform_update(self, serializer):
        # performed_exercise is writable, so a set can move to another
        # exercise - both the one it left and the one it joined change.
        previous_exercise_id = serializer.instance.performed_exercise.exercise_id
        exercise_set = serializer.save()
        records_services.rebuild_records_for_exercises(
            self.request.user,
            [previous_exercise_id, exercise_set.performed_exercise.exercise_id],
        )

    def perform_destroy(self, instance):
        exercise = instance.performed_exercise.exercise
        instance.delete()
        records_services.rebuild_records(self.request.user, exercise)


# --------------------------------------------------------------------
# Measurements
# --------------------------------------------------------------------


class MeasurementTypeViewSet(OwnedResourceViewSet):
    serializer_class = MeasurementTypeSerializer
    api_context = ApiContext.MEASUREMENTS

    def visible_queryset(self):
        return measurement_services.visible_to(self.request.user)


@extend_schema_view(
    create=extend_schema(parameters=[idempotency.SCHEMA_PARAMETER]),
    list=extend_schema(
        parameters=[
            OpenApiParameter("measurement_type", int, description="Only this type's readings."),
            OpenApiParameter(
                "recorded_after", description="Inclusive: recorded at or after this date/time."
            ),
            OpenApiParameter(
                "recorded_before", description="Exclusive: recorded before this date/time."
            ),
        ]
    )
)
class BodyMeasurementViewSet(IdempotentCreateMixin, viewsets.ModelViewSet):
    """`?measurement_type=` and `?recorded_after=`/`?recorded_before=`
    narrow the list (issue #29) - one type's history page without
    fetching every reading of every type."""

    serializer_class = BodyMeasurementSerializer
    api_context = ApiContext.MEASUREMENTS

    def get_queryset(self):
        queryset = BodyMeasurement.objects.filter(user=self.request.user)
        if self.action != "list":
            return queryset
        measurement_type = _query_id(self.request, "measurement_type")
        if measurement_type is not None:
            queryset = queryset.filter(measurement_type_id=measurement_type)
        recorded_after = _query_datetime(self.request, "recorded_after")
        if recorded_after is not None:
            queryset = queryset.filter(recorded_at__gte=recorded_after)
        recorded_before = _query_datetime(self.request, "recorded_before")
        if recorded_before is not None:
            queryset = queryset.filter(recorded_at__lt=recorded_before)
        return queryset

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


@extend_schema_view(
    create=extend_schema(parameters=[idempotency.SCHEMA_PARAMETER]),
    list=extend_schema(
        parameters=[
            OpenApiParameter("activity_type", int, description="Only this type's activities."),
            *_date_range_parameters(),
        ]
    )
)
class ActivityViewSet(IdempotentCreateMixin, viewsets.ModelViewSet):
    """`?activity_type=` and `?date_after=`/`?date_before=` narrow the
    list (issue #29)."""

    serializer_class = ActivitySerializer
    api_context = ApiContext.ACTIVITIES

    def get_queryset(self):
        queryset = Activity.objects.filter(user=self.request.user)
        if self.action != "list":
            return queryset
        activity_type = _query_id(self.request, "activity_type")
        if activity_type is not None:
            queryset = queryset.filter(activity_type_id=activity_type)
        return _filter_by_dates(self.request, queryset, "date")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# --------------------------------------------------------------------
# Records (read-only - PRs are derived, never directly writable)
# --------------------------------------------------------------------


class PersonalRecordViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = PersonalRecordSerializer
    api_context = ApiContext.RECORDS

    def get_queryset(self):
        return PersonalRecord.objects.filter(user=self.request.user).select_related("exercise")


# --------------------------------------------------------------------
# Analytics (read-only aggregates - no backing model of their own)
# --------------------------------------------------------------------


class LastPerformanceView(APIView):
    """`exercises/<id>/last-performance/` (issue #20): the sets from the
    user's most recent completed session that included the exercise
    (apps.workouts.services.last_performance), warmups flagged - for
    "last time: 3 x 5 @ 80 kg" or prefilling a new session's first set.
    Workouts context, since it reads logged sets; 404 for an exercise
    the user can't see, same as `exercises/<id>/`."""

    api_context = ApiContext.WORKOUTS

    # See TrainingSummaryView's own comment below for why this is here.
    @extend_schema(responses=LastPerformanceSerializer)
    def get(self, request, pk):
        exercise = get_object_or_404(
            exercise_services.visible_to(request.user, include_inactive=True), pk=pk
        )
        performed = workout_services.last_performance(request.user, exercise)
        data = {"session": None, "started_at": None, "sets": []}
        if performed is not None:
            data = {
                "session": performed.session_id,
                "started_at": performed.session.started_at,
                "sets": performed.sets.all(),
            }
        return Response(LastPerformanceSerializer(data, context={"request": request}).data)


class UpNextView(APIView):
    """The program and workout the dashboard offers next
    (apps.workouts.services.up_next, issue #16): `day` is the date a
    weekly program's workout is scheduled for, null for a continuous
    one. All three fields are null before the user has completed a
    workout from a program. Unlike the dashboard, this answers even
    while a session is in progress - `sessions/?status=in_progress`
    tells a client that. Only a suggestion: any workout can be
    started."""

    api_context = ApiContext.PROGRAMS

    # See TrainingSummaryView's own comment below for why this is here.
    @extend_schema(responses=UpNextSerializer)
    def get(self, request):
        upcoming = workout_services.up_next(request.user, timezone.localdate())
        data = {"program": None, "workout": None, "day": None}
        if upcoming is not None:
            data = {"program": upcoming.program, "workout": upcoming.workout, "day": upcoming.day}
        return Response(UpNextSerializer(data, context={"request": request}).data)


class TrainingSummaryView(APIView):
    """`?range=7d|30d|all` (default 30d, matching
    apps.analytics.views.AnalyticsDashboardView) - total sessions/
    duration/volume in canonical kg (see apps.api.serializers' own
    docstring for why this API never converts to a display unit)."""

    api_context = ApiContext.ANALYTICS

    # A plain APIView (not GenericAPIView), so it has no serializer_class
    # attribute for drf-spectacular's schema generator to discover on
    # its own (docs/API.md "Interactive docs") - without this it drops
    # the operation from the generated schema/Swagger UI entirely
    # rather than guessing wrong.
    @extend_schema(responses=TrainingSummarySerializer)
    def get(self, request):
        date_range = dateranges.resolve(request.query_params.get("range", "30d"))
        summary = analytics_services.training_summary_canonical(request.user, date_range)
        return Response(TrainingSummarySerializer(summary).data)


class AchievementsView(APIView):
    """The same all-time, shared-across-users highlights the dashboard
    carousel shows (apps.analytics.achievements) - see
    docs/UI.md "Achievements carousel" for what show_achievements does
    to this list."""

    api_context = ApiContext.ANALYTICS

    # See TrainingSummaryView's own comment above for why this is here.
    @extend_schema(responses=AchievementSerializer(many=True))
    def get(self, request):
        highlights = achievement_services.achievement_highlights()
        return Response(AchievementSerializer(highlights, many=True).data)


# --------------------------------------------------------------------
# Nutrition
# --------------------------------------------------------------------


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                "search",
                description="Matches the name or brand; a barcode matches `off_id` exactly.",
            ),
            OpenApiParameter("ids", description="Comma-separated ids, e.g. 1,2,3."),
        ]
    )
)
class FoodViewSet(OwnedResourceViewSet):
    """`?search=` and `?ids=` narrow the list (issue #28) - a client
    finds a food without downloading the whole library. Matched against
    the stored name and brand only, not a translation: unlike the
    exercise library, foods can run to thousands of rows, so the
    in-Python translated match exercises use doesn't apply (see
    apps.exercises.services.search)."""

    serializer_class = FoodSerializer
    api_context = ApiContext.NUTRITION

    def visible_queryset(self):
        from django.db.models import Q

        return Food.objects.filter(Q(owner=self.request.user) | Q(owner__isnull=True))

    def get_queryset(self):
        from django.db.models import Q

        queryset = super().get_queryset()
        if self.action != "list":
            return queryset
        queryset = _filter_by_ids(self.request, queryset)
        query = (self.request.query_params.get("search") or "").strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) | Q(brand__icontains=query) | Q(off_id=query)
            )
        return queryset


class MealSlotViewSet(OwnedResourceViewSet):
    serializer_class = MealSlotSerializer
    api_context = ApiContext.NUTRITION

    def visible_queryset(self):
        return nutrition_services.visible_meal_slots(self.request.user)


def _visible_recipes(user):
    """The user's own active recipes plus the built-in template ones
    (owner=None), the same set the web's recipe list and detail show
    (apps.nutrition.views._viewable_recipe_or_404, issue #38)."""
    from django.db.models import Q

    return Recipe.objects.filter(Q(owner=user) | Q(owner__isnull=True), active=True)


class RecipeViewSet(OwnedResourceViewSet):
    """Built-in recipes are listed and readable (a diet plan built by
    the wizard is full of them), `owner` null telling them apart; only
    the user's own are ever writable."""

    serializer_class = RecipeSerializer
    api_context = ApiContext.NUTRITION

    def visible_queryset(self):
        # RecipeSerializer nests every ingredient (with its food) -
        # prefetched here so listing many recipes stays two queries
        # total (recipes + ingredients), not one ingredients query per
        # recipe in the list.
        return _visible_recipes(self.request.user).prefetch_related("ingredients__food")

    def perform_destroy(self, instance):
        # Same as the web UI - a recipe still used by diary entries or
        # diet plans is only hidden (nutrition_services.delete_recipe).
        nutrition_services.delete_recipe(instance)


class RecipeIngredientViewSet(viewsets.ModelViewSet):
    serializer_class = RecipeIngredientSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        if self.action in ("update", "partial_update", "destroy"):
            return RecipeIngredient.objects.filter(
                recipe__owner=self.request.user, recipe__active=True
            )
        return RecipeIngredient.objects.filter(recipe__in=_visible_recipes(self.request.user))


@extend_schema_view(
    create=extend_schema(parameters=[idempotency.SCHEMA_PARAMETER]),
    list=extend_schema(
        parameters=[
            OpenApiParameter("date", description="Only this day's entries, YYYY-MM-DD."),
            *_date_range_parameters(),
        ]
    )
)
class DiaryEntryViewSet(IdempotentCreateMixin, viewsets.ModelViewSet):
    """`?date=` (one day, like the web diary) and `?date_after=`/
    `?date_before=` narrow the list (issue #26)."""

    serializer_class = DiaryEntrySerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        # food/recipe for each entry's name and, without a snapshot, its
        # nutrition (DiaryEntrySerializer).
        queryset = DiaryEntry.objects.filter(user=self.request.user).select_related(
            "food", "recipe"
        )
        if self.action != "list":
            return queryset
        day = _query_date(self.request, "date")
        if day is not None:
            queryset = queryset.filter(date=day)
        return _filter_by_dates(self.request, queryset, "date")

    @extend_schema(request=DiaryCopyDaySerializer, responses={201: DiaryCopyDayResultSerializer})
    @action(detail=False, methods=["post"], url_path="copy-day")
    def copy_day(self, request):
        """Copies one day's whole diary onto another date
        (apps.nutrition.services.copy_diary_day), like the web's "Copy
        day": each copy keeps the source entry's nutrition, and entries
        of a since-deleted recipe are copied too. Purely additive."""
        serializer = DiaryCopyDaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        copied = nutrition_services.copy_diary_day(
            request.user,
            serializer.validated_data["source_date"],
            serializer.validated_data["target_date"],
        )
        return Response({"copied": copied}, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class NutritionGoalViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only - see NutritionGoalSerializer's own docstring for why:
    a goal is only ever created/superseded through
    apps.nutrition.services.set_goal, never a raw PATCH."""

    serializer_class = NutritionGoalSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return NutritionGoal.objects.filter(user=self.request.user)


class NutritionTargetViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only - same reasoning as NutritionGoalViewSet above."""

    serializer_class = NutritionTargetSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return NutritionTarget.objects.filter(user=self.request.user)


class NutritionProfileView(generics.RetrieveUpdateAPIView):
    """A singleton resource, the same shape as ProfileView above -
    GET/PATCH always act on the authenticated key's own
    NutritionProfile. get_object_or_404 rather than a bare attribute
    lookup: a user who hasn't gone through nutrition onboarding yet
    has no NutritionProfile row at all, and that's a plain 404, not a
    500."""

    serializer_class = NutritionProfileSerializer
    api_context = ApiContext.NUTRITION

    def get_object(self):
        return get_object_or_404(NutritionProfile, user=self.request.user)


class DailyTotalsView(APIView):
    """`nutrition/daily-totals/?date=YYYY-MM-DD` (default today): the
    day's diary added up (apps.nutrition.services.daily_totals) - the
    same total the web diary's header shows, from each entry's logged
    snapshot (issue #27)."""

    api_context = ApiContext.NUTRITION

    # See TrainingSummaryView's own comment for why this is here.
    @extend_schema(
        responses=DailyTotalsSerializer,
        parameters=[OpenApiParameter("date", description="YYYY-MM-DD; default today.")],
    )
    def get(self, request):
        day = _query_date(request, "date") or timezone.localdate()
        totals = nutrition_services.daily_totals(request.user, day)
        return Response(DailyTotalsSerializer({"date": day, "totals": totals}).data)


class DietPlanViewSet(viewsets.ModelViewSet):
    """create/activate/deactivate/apply all go through
    apps.nutrition.diet_builder/services rather than a raw field
    write - see DietPlanSerializer's own docstring for why."""

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
    # validated_data on create too, not just update) - so "read-only
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

    @extend_schema(
        responses=ShoppingListSerializer,
        parameters=[
            OpenApiParameter(
                "trip", int, required=False,
                description="The trip's shopping weekday, 0 (Monday) to 6 (Sunday). "
                "Default: the trip shopped today or next.",
            )
        ],
    )
    @action(detail=True, methods=["get"], url_path="shopping-list")
    def shopping_list(self, request, pk=None):
        """The plan's shopping list for one trip (apps.nutrition.shopping):
        every food the trip's days need - recipes broken into ingredients
        - added up, with each day's amount. `?trip=<weekday>` picks the
        trip starting that day; by default the one shopped today or next.
        Derived from the plan on every request; nothing is stored."""
        from apps.nutrition import shopping

        plan = self.get_object()
        trips = shopping.plan_trips(plan)
        raw_trip = request.query_params.get("trip")
        if raw_trip is not None and not raw_trip.isdigit():
            return Response(
                {"trip": "Must be a weekday number, 0 (Monday) to 6 (Sunday)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        trip = shopping.pick_trip(
            trips, int(raw_trip) if raw_trip is not None else None,
            timezone.localdate().weekday(),
        )
        if trip is None:
            return Response(
                {"trip": "Not one of this plan's shopping days."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = shopping.shopping_list(plan, trip)
        data = {
            "trip": trip,
            "trips": trips,
            "include_shopping_day": plan.shopping_includes_shopping_day,
            "items": [
                {
                    "food": row.food,
                    "unit": row.unit,
                    "total": row.total,
                    "by_day": [
                        {"weekday": day, "quantity": quantity} for day, quantity in row.ordered_days
                    ],
                }
                for row in rows
            ],
        }
        return Response(ShoppingListSerializer(data).data)

    @extend_schema(request=ShoppingSettingsSerializer, responses=ShoppingSettingsSerializer)
    @action(detail=True, methods=["get", "put", "patch"], url_path="shopping-settings")
    def shopping_settings(self, request, pk=None):
        """The plan's shopping days and include/exclude choice. PUT
        replaces both; PATCH changes whichever is given."""
        from apps.nutrition import shopping

        plan = self.get_object()
        current = {
            "weekdays": list(plan.shopping_days.values_list("weekday", flat=True)),
            "include_shopping_day": plan.shopping_includes_shopping_day,
        }
        if request.method == "GET":
            return Response(ShoppingSettingsSerializer(current).data)
        serializer = ShoppingSettingsSerializer(
            data=request.data, partial=request.method == "PATCH"
        )
        serializer.is_valid(raise_exception=True)
        settings = {**current, **serializer.validated_data}
        shopping.set_shopping_settings(
            plan, settings["weekdays"], settings["include_shopping_day"]
        )
        return Response(
            ShoppingSettingsSerializer(
                {
                    "weekdays": sorted(set(settings["weekdays"])),
                    "include_shopping_day": settings["include_shopping_day"],
                }
            ).data
        )

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        """Materializes this plan into real DiaryEntry rows for
        `date` - the one thing an API consumer would otherwise have
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
    """Read-only - see DietPlanSerializer's own docstring for why."""

    serializer_class = DietPlanMealSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return DietPlanMeal.objects.filter(diet_plan__user=self.request.user)


class DietPlanItemViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only - same reasoning as DietPlanMealViewSet above."""

    serializer_class = DietPlanItemSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return DietPlanItem.objects.filter(diet_plan_meal__diet_plan__user=self.request.user)


# --------------------------------------------------------------------
# Stretching
# --------------------------------------------------------------------


class StretchViewSet(OwnedResourceViewSet):
    serializer_class = StretchSerializer
    api_context = ApiContext.STRETCHING

    def visible_queryset(self):
        return stretching_services.visible_stretches(self.request.user).prefetch_related(
            "muscle_groups"
        )


class StretchRoutineViewSet(OwnedResourceViewSet):
    serializer_class = StretchRoutineSerializer
    api_context = ApiContext.STRETCHING

    def visible_queryset(self):
        return stretching_services.visible_routines(self.request.user).prefetch_related("items")


class RoutineItemViewSet(viewsets.ModelViewSet):
    serializer_class = RoutineItemSerializer
    api_context = ApiContext.STRETCHING

    def get_queryset(self):
        return RoutineItem.objects.filter(routine__owner=self.request.user)


@extend_schema_view(create=extend_schema(parameters=[idempotency.SCHEMA_PARAMETER]))
class StretchSessionViewSet(IdempotentCreateMixin, viewsets.ModelViewSet):
    serializer_class = StretchSessionSerializer
    api_context = ApiContext.STRETCHING

    def get_queryset(self):
        return stretching_services.sessions_for(self.request.user).prefetch_related(
            "performed_stretches"
        )

    def perform_create(self, serializer):
        data = serializer.validated_data
        serializer.instance = stretching_services.quick_log(
            self.request.user,
            date=data.get("date") or timezone.localdate(),
            duration=data["duration"],
            routine=data.get("routine"),
            notes=data.get("notes", ""),
        )
