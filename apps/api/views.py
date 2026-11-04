"""DRF views for every apps.api context - see docs/API.md.

Every viewset here goes through the exact same domain services the
server-rendered web views already use (apps.exercises.services,
apps.programs.services, apps.workouts.services, apps.records.services,
...) rather than re-deriving ownership/visibility rules or
snapshot-on-start/PR-detection side effects a second time - per
CLAUDE.md, business logic belongs in services, and a DRF view is exactly
as much a "view" as a Django one in that sense.
"""

import zoneinfo
from contextlib import nullcontext
from datetime import datetime, time
from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.utils import timezone, translation
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.activities import services as activity_services
from apps.activities.models import Activity
from apps.analytics import achievements as achievement_services
from apps.analytics import dateranges
from apps.analytics import services as analytics_services
from apps.exercises import services as exercise_services
from apps.exercises.forms import ExerciseImageForm
from apps.exercises.models import Equipment, ExerciseImage, MuscleGroup
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
    AdjustmentSuggestionSerializer,
    BodyMeasurementSerializer,
    CooldownSuggestionSerializer,
    DailyTotalsSerializer,
    DiaryCopyDayResultSerializer,
    DiaryCopyDaySerializer,
    DiaryEntrySerializer,
    DietPlanItemSerializer,
    DietPlanMealSerializer,
    DietPlanSerializer,
    EquipmentSerializer,
    ExerciseAnalyticsSerializer,
    ExerciseImageSerializer,
    ExerciseImageUploadSerializer,
    ExercisePrescriptionSerializer,
    ExerciseSerializer,
    ExerciseSetCreatedSerializer,
    ExerciseSetSerializer,
    FoodImportOffSerializer,
    FoodSerializer,
    LastPerformanceSerializer,
    MaintenanceEstimateSerializer,
    MealSlotSerializer,
    MeasurementTypeSerializer,
    MuscleGroupAnalyticsSerializer,
    MuscleGroupSerializer,
    NutritionGoalCreatedSerializer,
    NutritionGoalCreateSerializer,
    NutritionGoalSerializer,
    NutritionProfileSerializer,
    NutritionStatsSerializer,
    NutritionTargetSerializer,
    OffSearchSerializer,
    PerformedExerciseSerializer,
    PerformedStretchMarkSerializer,
    PerformedStretchSerializer,
    PersonalRecordSerializer,
    ProfileSerializer,
    ProgramSerializer,
    RecentActivitySerializer,
    RecipeIngredientSerializer,
    RecipeSerializer,
    RemainingEstimateSerializer,
    RoutineItemSerializer,
    ShoppingListSerializer,
    ShoppingSettingsSerializer,
    StretchRoutineSerializer,
    StretchSerializer,
    StretchSessionQuickLogSerializer,
    StretchSessionSerializer,
    StretchSessionStartSerializer,
    SuggestionSerializer,
    TrainingSummaryResponseSerializer,
    UpNextSerializer,
    VolumeAnalyticsSerializer,
    WorkoutSerializer,
    WorkoutSessionSerializer,
    duration_estimate_data,
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


def _own_exercise_or_403(exercise, user):
    """A visible exercise whose images the user may manage - their own
    custom one. A built-in's images are admin-only (403), like every
    other edit to it; an invisible one is already a 404."""
    if exercise.owner_id != user.pk:
        raise PermissionDenied("Images of built-in exercises are managed by an administrator.")
    return exercise


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

    def _own_exercise(self):
        return _own_exercise_or_403(self.get_object(), self.request.user)

    @extend_schema(
        request={"multipart/form-data": ExerciseImageUploadSerializer},
        responses={201: ExerciseImageSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="images",
        parser_classes=[MultiPartParser, FormParser],
    )
    def add_image(self, request, pk=None):
        """`POST exercises/<id>/images/` (multipart: `image`, optional
        `caption`, `order`) - issue #48. Through the web's own
        ExerciseImageForm, so the size limit, the image checks and
        `max_images_per_exercise` apply; 400 with its messages otherwise."""
        exercise = self._own_exercise()
        upload = ExerciseImageUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)
        with _user_language(request.user):
            form = ExerciseImageForm(
                {"caption": upload.validated_data.get("caption", "")},
                request.FILES,
                instance=ExerciseImage(
                    exercise=exercise, order=upload.validated_data.get("order", 0)
                ),
            )
            if not form.is_valid():
                raise serializers.ValidationError(
                    {
                        ("non_field_errors" if field == "__all__" else field): errors
                        for field, errors in form.errors.items()
                    }
                )
        image = form.save()
        return Response(
            ExerciseImageSerializer(image, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


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

    def perform_update(self, serializer):
        # Every edit bumps the version, like the web's ProgramUpdateView -
        # it's what offers a coach's clients the update (GitHub issue #46).
        serializer.save().bump_version()

    @extend_schema(request=None, responses={201: ProgramSerializer})
    @action(detail=True, methods=["post"])
    def copy(self, request, pk=None):
        """The user's own editable copy of any visible program - workouts
        and prescriptions included, in one step (programs.services.
        copy_program, the web's "Copy", issue #32)."""
        new_program = program_services.copy_program(self.get_object(), owner=request.user)
        new_program = self.visible_queryset().get(pk=new_program.pk)
        return Response(
            self.get_serializer(new_program).data, status=status.HTTP_201_CREATED
        )


# Creating, editing or deleting a program's workout or prescription
# bumps the program's version, the same as the web's views in
# apps.programs.views - without it a coach's edit over the API never
# offers their clients' imported copies an update (GitHub issue #46).
# An edit moving a row to another program bumps both. (A comment, not a
# docstring: drf-spectacular would put a docstring into the schema.)
class _BumpsProgramVersionMixin:
    def _program_of(self, instance):
        raise NotImplementedError

    def perform_create(self, serializer):
        self._program_of(serializer.save()).bump_version()

    def perform_update(self, serializer):
        before = self._program_of(serializer.instance)
        after = self._program_of(serializer.save())
        after.bump_version()
        if before.pk != after.pk:
            before.bump_version()

    def perform_destroy(self, instance):
        program = self._program_of(instance)
        instance.delete()
        program.bump_version()


class WorkoutViewSet(_BumpsProgramVersionMixin, viewsets.ModelViewSet):
    serializer_class = WorkoutSerializer
    api_context = ApiContext.PROGRAMS

    def _program_of(self, instance):
        return instance.program

    def get_queryset(self):
        if self.action in ("update", "partial_update", "destroy"):
            programs = program_services.editable_by(self.request.user)
        else:
            programs = program_services.visible_to(self.request.user)
        return Workout.objects.filter(program__in=programs).prefetch_related(
            "prescriptions__exercise"
        )


class ExercisePrescriptionViewSet(_BumpsProgramVersionMixin, viewsets.ModelViewSet):
    serializer_class = ExercisePrescriptionSerializer
    api_context = ApiContext.PROGRAMS

    def _program_of(self, instance):
        return instance.workout.program

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


def _user_language(user):
    """The user's language, active for text built in this request: an
    API key's request never passes through UserLanguageMiddleware with
    its user (DRF authenticates later)."""
    return translation.override(user.language) if user.language else nullcontext()


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

    @extend_schema(responses={200: RemainingEstimateSerializer})
    @action(detail=True, methods=["get"], url_path="remaining-estimate")
    def remaining_estimate(self, request, pk=None):
        """What's left of a workout in progress (set_timing.
        estimate_remaining, training mode's own figure - issue #50)."""
        from apps.workouts import set_timing

        session = self.get_object()
        estimate = None
        if session.is_in_progress:
            performed = session.performed_exercises.select_related("exercise").prefetch_related(
                "sets"
            )
            estimate = set_timing.estimate_remaining(list(performed))
        return Response(
            RemainingEstimateSerializer({"estimate": duration_estimate_data(estimate)}).data
        )

    @extend_schema(responses={200: CooldownSuggestionSerializer})
    @action(detail=True, methods=["get"])
    def cooldown(self, request, pk=None):
        """The cool-down suggestion for one finished workout, and the
        cool-down already done after it (issue #43). Needs the key's
        stretching read permission too; 409 while the workout isn't
        completed."""
        from rest_framework.exceptions import PermissionDenied

        from .permissions import has_context_permission

        if not has_context_permission(request, ApiContext.STRETCHING):
            raise PermissionDenied("This key can't read stretching.")
        session = self.get_object()
        if session.status != WorkoutSessionStatus.COMPLETED:
            return Response(
                {"detail": "This workout isn't completed."}, status=status.HTTP_409_CONFLICT
            )
        suggestion = stretching_services.suggest_cooldown(session)
        data = {
            "muscle_groups": suggestion.muscle_groups if suggestion else [],
            "routine": suggestion.routine if suggestion else None,
            "stretches": suggestion.stretches if suggestion else [],
            "planned_seconds": (
                stretching_services.cooldown_seconds(suggestion) if suggestion else None
            ),
            "done": stretching_services.cooldown_done_after(session),
        }
        return Response(CooldownSuggestionSerializer(data, context={"request": request}).data)


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


_RANGE_PARAMETER = OpenApiParameter(
    "range",
    enum=["week", "7d", "30d", "3m", "6m", "1y", "all"],
    description=(
        "Default 30d. `week` is Monday of the current ISO week through today, in the "
        "user's time zone."
    ),
)


class UserTimezoneMixin:
    """Runs the view in the user's own time zone, as the web does: an API
    key's request never passes through UserTimezoneMiddleware with its
    user (DRF authenticates later), so `timezone.localdate()` and date
    lookups would otherwise use the server's zone. That middleware's
    `timezone.deactivate()` still resets it after the response. Only for
    views whose response has dates, not datetimes (those stay UTC)."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            timezone.activate(zoneinfo.ZoneInfo(request.user.timezone))
        except (ValueError, zoneinfo.ZoneInfoNotFoundError):
            # Empty, or a stale/hand-edited zone name: the server's, like the web.
            pass


def _analytics_range(request):
    """`?range=` (default 30d) as a DateRange ending today - in the user's
    time zone under UserTimezoneMixin, so `week` (issue #49) starts on the
    same Monday as the web dashboard's "this week"."""
    return dateranges.resolve(request.query_params.get("range", "30d"))


class ExerciseImageDeleteView(APIView):
    """`DELETE exercises/<id>/images/<image id>/` - removes the file too,
    like the web's exercise_image_delete (issue #48)."""

    api_context = ApiContext.EXERCISES

    @extend_schema(request=None, responses={204: None})
    def delete(self, request, pk, image_pk):
        exercise = _own_exercise_or_403(
            get_object_or_404(exercise_services.visible_to(request.user), pk=pk), request.user
        )
        image = get_object_or_404(ExerciseImage, pk=image_pk, exercise=exercise)
        image.image.delete(save=False)
        image.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TrainingSummaryView(UserTimezoneMixin, APIView):
    """`?range=week|7d|30d|3m|6m|1y|all` (default 30d, matching
    apps.analytics.views.AnalyticsDashboardView) - total sessions/
    duration/volume in canonical kg (see apps.api.serializers' own
    docstring for why this API never converts to a display unit)."""

    api_context = ApiContext.ANALYTICS

    # A plain APIView (not GenericAPIView), so it has no serializer_class
    # attribute for drf-spectacular's schema generator to discover on
    # its own (docs/API.md "Interactive docs") - without this it drops
    # the operation from the generated schema/Swagger UI entirely
    # rather than guessing wrong.
    @extend_schema(responses=TrainingSummaryResponseSerializer, parameters=[_RANGE_PARAMETER])
    def get(self, request):
        date_range = _analytics_range(request)
        summary = analytics_services.training_summary_canonical(request.user, date_range)
        return Response(
            TrainingSummaryResponseSerializer({**vars(summary), "range": date_range}).data
        )


class VolumeAnalyticsView(UserTimezoneMixin, APIView):
    """`analytics/volume/?range=` - training volume per ISO week (and
    `analytics/muscle-groups/` per primary muscle group), the web
    Analytics page's bar charts as data (issue #35). Canonical kg."""

    api_context = ApiContext.ANALYTICS

    # See TrainingSummaryView's own comment for why this is here.
    @extend_schema(responses=VolumeAnalyticsSerializer, parameters=[_RANGE_PARAMETER])
    def get(self, request):
        date_range = _analytics_range(request)
        weeks = [
            {"week_start": week, "volume": volume}
            for week, volume in analytics_services.weekly_volume(request.user, date_range)
        ]
        return Response(VolumeAnalyticsSerializer({"range": date_range, "weeks": weeks}).data)


class MuscleGroupAnalyticsView(UserTimezoneMixin, APIView):
    """`analytics/muscle-groups/?range=` - see VolumeAnalyticsView. A
    set counts in full toward each of its exercise's primary muscle
    groups, as on the web."""

    api_context = ApiContext.ANALYTICS

    @extend_schema(responses=MuscleGroupAnalyticsSerializer, parameters=[_RANGE_PARAMETER])
    def get(self, request):
        date_range = _analytics_range(request)
        rows = [
            {"muscle_group": muscle_group, "volume": volume}
            for muscle_group, volume in analytics_services.muscle_group_volume(
                request.user, date_range
            )
        ]
        return Response(
            MuscleGroupAnalyticsSerializer(
                {"range": date_range, "muscle_groups": rows}, context={"request": request}
            ).data
        )


class ExerciseAnalyticsView(UserTimezoneMixin, APIView):
    """`analytics/exercises/<id>/?range=` - one exercise's progress:
    sessions and volume in range, and its best set per training day by
    estimated 1RM, the web's strength trend (issue #35). Canonical kg;
    404 for an exercise the user can't see."""

    api_context = ApiContext.ANALYTICS

    @extend_schema(responses=ExerciseAnalyticsSerializer, parameters=[_RANGE_PARAMETER])
    def get(self, request, pk):
        exercise = get_object_or_404(
            exercise_services.visible_to(request.user, include_inactive=True), pk=pk
        )
        date_range = _analytics_range(request)
        summary = analytics_services.exercise_summary_canonical(request.user, exercise, date_range)
        bests = analytics_services.exercise_session_bests(request.user, exercise, date_range)
        return Response(
            ExerciseAnalyticsSerializer(
                {
                    "range": date_range,
                    "exercise": exercise,
                    "session_count": summary.session_count,
                    "total_volume": summary.total_volume,
                    "sessions": bests,
                }
            ).data
        )


class RecentlyActiveView(APIView):
    """`analytics/recently-active/` - the dashboard's "Recently active"
    list (apps.analytics.achievements.recently_active_users, issue
    #37): every user who shares their training (`show_achievements`),
    by when they last trained, `is_in_progress` while they're training
    now. Shared across users, like achievements."""

    api_context = ApiContext.ANALYTICS

    # See TrainingSummaryView's own comment for why this is here.
    @extend_schema(responses=RecentActivitySerializer(many=True))
    def get(self, request):
        activity = achievement_services.recently_active_users()
        return Response(RecentActivitySerializer(activity, many=True).data)


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

    @extend_schema(
        request=FoodImportOffSerializer,
        responses={200: FoodSerializer, 201: FoodSerializer},
    )
    @action(detail=False, methods=["post"], url_path="import-off")
    def import_off(self, request):
        """Saves an Open Food Facts product by barcode, the same way the
        web does (services.import_or_refresh_food_from_off, issue #41):
        a shared food with its Nutri-Score, NOVA group and OFF link.
        200 with the existing food when the barcode is already known
        (refreshed if stale), 201 when it's new, 404 when OFF has no
        usable product (or couldn't be reached), 429 when the server's
        Open Food Facts budget is spent, 503 when the integration is
        turned off."""
        from apps.nutrition import openfoodfacts
        from apps.nutrition.models import OpenFoodFactsSettings

        serializer = FoodImportOffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        barcode = serializer.validated_data["barcode"]
        if not OpenFoodFactsSettings.load().enabled:
            return Response(
                {"detail": "Open Food Facts is turned off on this server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        existed = Food.objects.filter(off_id=barcode).exists()
        try:
            food = nutrition_services.import_or_refresh_food_from_off(
                barcode, raise_on_rate_limit=True
            )
        except openfoodfacts.OpenFoodFactsRateLimited:
            food = Food.objects.filter(off_id=barcode).first()
            if food is None:
                return Response(
                    {"detail": "Open Food Facts is busy, try again in a minute."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
        if food is None:
            return Response(
                {"detail": "Open Food Facts has no usable product for this barcode."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            FoodSerializer(food).data,
            status=status.HTTP_200_OK if existed else status.HTTP_201_CREATED,
        )

    @extend_schema(
        responses=OffSearchSerializer,
        parameters=[OpenApiParameter("q", required=True, description="Text or a barcode.")],
    )
    @action(detail=False, methods=["get"], url_path="search-off")
    def search_off(self, request):
        """Searches the visible foods and Open Food Facts together, as
        the web's "Search Open Food Facts" button does
        (services.search_foods with online=True) - through the server's
        cache and rate budget, shared by every client. `local` are foods
        already here; `results` are OFF products not imported yet.
        Call it once per user action, not while typing."""
        query = (request.query_params.get("q") or "").strip()
        if not query:
            raise serializers.ValidationError({"q": "This field is required."})
        language = request.user.language
        with translation.override(language) if language else nullcontext():
            local, results, off_status = nutrition_services.search_foods(
                request.user, query, online=True
            )
        return Response(
            OffSearchSerializer(
                {"status": off_status, "local": local, "results": results}
            ).data
        )


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


@extend_schema_view(
    create=extend_schema(
        request=NutritionGoalCreateSerializer, responses={201: NutritionGoalCreatedSerializer}
    )
)
class NutritionGoalViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Goals are historized: never edited or deleted, only superseded.
    `POST` starts a new one through apps.nutrition.services.change_goal,
    the same as the web's goal page (issue #33): the open goal and
    target are ended and a target is calculated for the new goal from
    the nutrition profile, height and latest body weight - a 400 says
    which is missing."""

    serializer_class = NutritionGoalSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return NutritionGoal.objects.filter(user=self.request.user)

    def create(self, request):
        serializer = NutritionGoalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with _user_language(request.user):
            try:
                goal, target = nutrition_services.change_goal(
                    request.user,
                    goal_type=data["goal_type"],
                    target_rate_kg_per_week=data["target_rate_kg_per_week"],
                    target_weight=data.get("target_weight"),
                    notes=data.get("notes", ""),
                )
            except nutrition_services.GoalChangeError as error:
                return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
            goal.target = target
            body = NutritionGoalCreatedSerializer(goal).data
        return Response(body, status=status.HTTP_201_CREATED)


class NutritionTargetViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only - same reasoning as NutritionGoalViewSet above."""

    serializer_class = NutritionTargetSerializer
    api_context = ApiContext.NUTRITION

    def get_queryset(self):
        return NutritionTarget.objects.filter(user=self.request.user)


class AdjustmentSuggestionView(APIView):
    """`GET nutrition/adjustment-suggestion/` - the nutrition dashboard's
    calorie adjustment card (apps.nutrition.suggestions.
    suggest_calorie_adjustment, issue #33). Only ever a suggestion."""

    api_context = ApiContext.NUTRITION

    # See TrainingSummaryView's own comment for why this is here.
    @extend_schema(responses=AdjustmentSuggestionSerializer)
    def get(self, request):
        from apps.nutrition.suggestions import suggest_calorie_adjustment

        with _user_language(request.user):
            suggestion = suggest_calorie_adjustment(request.user)
            return Response(AdjustmentSuggestionSerializer(suggestion).data)


class MaintenanceEstimateView(APIView):
    """`GET nutrition/maintenance/` - the nutrition dashboard's "Observed
    maintenance" card (apps.nutrition.maintenance, issue #44)."""

    api_context = ApiContext.NUTRITION

    # See TrainingSummaryView's own comment for why this is here.
    @extend_schema(responses=MaintenanceEstimateSerializer)
    def get(self, request):
        from dataclasses import asdict

        from apps.nutrition import maintenance

        with _user_language(request.user):
            estimate = maintenance.estimate_maintenance(request.user)
            data = {**asdict(estimate), "window_days": maintenance.WINDOW_DAYS}
            return Response(MaintenanceEstimateSerializer(data).data)


class NutritionStatsView(APIView):
    """`GET nutrition/stats/?range=` - the nutrition statistics page
    (apps.nutrition.services.nutrition_stats_by_period and
    calorie_history, issue #44). `range` is one of the page's fixed
    periods, default `30d`."""

    api_context = ApiContext.NUTRITION

    @extend_schema(
        responses=NutritionStatsSerializer,
        parameters=[
            OpenApiParameter(
                "range", enum=nutrition_services.NUTRITION_STATS_PERIOD_KEYS,
                description="One of the fixed periods; default 30d.",
            )
        ],
    )
    def get(self, request):
        from apps.nutrition.models import NutritionTarget

        key = request.query_params.get("range", "30d")
        if key not in nutrition_services.NUTRITION_STATS_PERIOD_KEYS:
            return Response(
                {
                    "range": "Must be one of: "
                    f"{', '.join(nutrition_services.NUTRITION_STATS_PERIOD_KEYS)}."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        with _user_language(request.user):
            periods = [
                {**vars(period), "label": str(period.label)}
                for period in nutrition_services.nutrition_stats_by_period(request.user)
            ]
        for period in periods:
            summary = period["summary"]
            period["summary"] = {
                **vars(summary), "possible_logging_gaps": summary.possible_logging_gaps
            }
        period = next(period for period in periods if period["key"] == key)
        today = timezone.localdate()
        history = nutrition_services.calorie_history(
            request.user, days=max((today - period["start"]).days + 1, 1)
        )
        target = NutritionTarget.objects.filter(user=request.user, ended_at__isnull=True).first()
        data = {
            "period": period,
            "daily": [
                {"date": day, "calories": totals and int(totals.calories.quantize(Decimal("1")))}
                for day, totals in history
            ],
            "target_calories": target.daily_calories if target else None,
            "periods": periods,
        }
        return Response(NutritionStatsSerializer(data).data)


class AdjustmentSuggestionAcceptView(APIView):
    """`POST nutrition/adjustment-suggestion/accept/` - accepting the
    suggestion, the web's accept_adjustment_suggestion: the suggestion
    is worked out again here and, when it's `adjust`, a new target
    (source `adjusted`) replaces the current one - 201 with it. A 409
    with the current suggestion when there's nothing to accept."""

    api_context = ApiContext.NUTRITION

    @extend_schema(request=None, responses={201: NutritionTargetSerializer})
    def post(self, request):
        from apps.nutrition.suggestions import AdjustmentAction, suggest_calorie_adjustment

        with _user_language(request.user):
            suggestion = suggest_calorie_adjustment(request.user)
            if suggestion.action != AdjustmentAction.ADJUST:
                return Response(
                    {
                        "detail": "There's no adjustment to accept.",
                        "suggestion": AdjustmentSuggestionSerializer(suggestion).data,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            target = nutrition_services.apply_adjustment_suggestion(request.user, suggestion)
            return Response(
                NutritionTargetSerializer(target).data, status=status.HTTP_201_CREATED
            )


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

    def put(self, request, *args, **kwargs):
        """A `PUT` before onboarding creates the profile (201) - the
        first step of setting up nutrition through the API (issue #33),
        before `POST nutrition-goals/`."""
        if NutritionProfile.objects.filter(user=request.user).exists():
            return super().put(request, *args, **kwargs)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


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
    _SNAPSHOT_FIELDS = ("name", "is_weekly")

    def perform_update(self, serializer):
        for field in self._UPDATE_ONLY_DISCARDED_FIELDS:
            serializer.validated_data.pop(field, None)
        before = {field: getattr(serializer.instance, field) for field in self._SNAPSHOT_FIELDS}
        plan = serializer.save()
        # `name` and `is_weekly` are part of what a coach's client
        # imports (nutrition.services.export_diet_plan), so changing
        # them offers the clients an update, like the program edits in
        # GitHub issue #46. `goal` isn't exported, so it doesn't.
        if any(getattr(plan, field) != value for field, value in before.items()):
            plan.bump_version()

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

    @extend_schema(request=None, responses={201: StretchRoutineSerializer})
    @action(detail=True, methods=["post"])
    def copy(self, request, pk=None):
        """The user's own editable copy of any visible routine, items
        included (stretching.services.copy_routine, issue #32). A
        built-in routine's name is translated into the user's language
        on the way, as on the web."""
        with _user_language(request.user):
            routine = stretching_services.copy_routine(self.get_object(), request.user)
        return Response(self.get_serializer(routine).data, status=status.HTTP_201_CREATED)


class RoutineItemViewSet(viewsets.ModelViewSet):
    serializer_class = RoutineItemSerializer
    api_context = ApiContext.STRETCHING

    def get_queryset(self):
        return RoutineItem.objects.filter(routine__owner=self.request.user)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                "after_workout", int,
                description="Only the cool-downs done after this workout session.",
            )
        ]
    ),
    create=extend_schema(
        parameters=[idempotency.SCHEMA_PARAMETER],
        request=StretchSessionQuickLogSerializer,
        responses={201: StretchSessionSerializer},
    )
)
class StretchSessionViewSet(IdempotentCreateMixin, viewsets.ModelViewSet):
    """`POST` is a quick log; a guided session goes `start/` ->
    `mark/` per stretch -> `complete/` (or `abandon/`), the same
    services as the web player (issue #34). Only one guided session can
    be in progress at a time."""

    serializer_class = StretchSessionSerializer
    api_context = ApiContext.STRETCHING

    def get_queryset(self):
        queryset = stretching_services.sessions_for(self.request.user).prefetch_related(
            "performed_stretches"
        )
        if self.action == "list":
            raw = self.request.query_params.get("after_workout")
            if raw is not None:
                if not raw.isdigit():
                    raise serializers.ValidationError({"after_workout": "Must be a session id."})
                queryset = queryset.filter(after_workout_id=int(raw))
        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return StretchSessionQuickLogSerializer
        return StretchSessionSerializer

    def perform_create(self, serializer):
        data = serializer.validated_data
        serializer.instance = stretching_services.quick_log(
            self.request.user,
            date=data.get("date") or timezone.localdate(),
            duration=data["duration"],
            routine=data.get("routine"),
            notes=data.get("notes", ""),
            after_workout=data.get("after_workout"),
        )

    def _in_progress_or_409(self):
        from apps.stretching.models import StretchSessionStatus

        session = self.get_object()
        if session.status != StretchSessionStatus.IN_PROGRESS:
            return session, Response(
                {"detail": "This session isn't in progress."}, status=status.HTTP_409_CONFLICT
            )
        return session, None

    @extend_schema(
        request=StretchSessionStartSerializer, responses={201: StretchSessionSerializer}
    )
    @action(detail=False, methods=["post"])
    def start(self, request):
        """Starts a guided session from `routine`, or from `stretches`
        at their default holds, snapshotting the plan like the web does
        (services.start_session). `after_workout` marks it as that
        finished workout's cool-down. 409 with the session's `id` when
        one is already in progress - resume that one instead."""
        serializer = StretchSessionStartSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            with _user_language(request.user):
                session = stretching_services.start_session(
                    request.user,
                    routine=data.get("routine"),
                    stretches=data.get("stretches") or None,
                    name=data.get("name", ""),
                    after_workout=data.get("after_workout"),
                )
        except stretching_services.SessionAlreadyInProgress as exc:
            return Response(
                {"detail": str(exc), "id": exc.session.pk}, status=status.HTTP_409_CONFLICT
            )
        session = self.get_queryset().get(pk=session.pk)
        return Response(
            StretchSessionSerializer(session, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(request=PerformedStretchMarkSerializer, responses=PerformedStretchSerializer)
    @action(detail=True, methods=["post"])
    def mark(self, request, pk=None):
        """Marks one of the session's stretches done (optionally with the
        seconds actually held) or skipped (services.mark_performed)."""
        session, conflict = self._in_progress_or_409()
        if conflict:
            return conflict
        serializer = PerformedStretchMarkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        performed = get_object_or_404(
            session.performed_stretches.all(), pk=data["performed_stretch"]
        )
        stretching_services.mark_performed(
            performed, done=data["done"], actual_seconds=data.get("actual_seconds")
        )
        return Response(
            PerformedStretchSerializer(performed, context={"request": request}).data
        )

    @extend_schema(request=None, responses=StretchSessionSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """Finishes the guided session; its duration is the time since
        it started (services.complete_session)."""
        session, conflict = self._in_progress_or_409()
        if conflict:
            return conflict
        stretching_services.complete_session(session)
        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data)

    @extend_schema(request=None, responses=StretchSessionSerializer)
    @action(detail=True, methods=["post"])
    def abandon(self, request, pk=None):
        session, conflict = self._in_progress_or_409()
        if conflict:
            return conflict
        stretching_services.abandon_session(session)
        return Response(self.get_serializer(self.get_queryset().get(pk=session.pk)).data)
