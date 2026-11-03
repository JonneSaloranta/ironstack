"""DRF serializers for every apps.api context - see docs/API.md.

Deliberate design choice covering every serializer here: all weights are
canonical kilograms and all lengths are canonical meters, never converted
to a user's display-unit preference the way the server-rendered UI does.
A machine API consumer needs an unambiguous unit it can rely on
regardless of who's calling it; "kg" always meaning kg is that contract.
`unit_system`/`show_bmi`/etc. on the profile endpoint are still exposed
as plain preferences (the UI reads them to decide *how* to render), just
never applied to convert any other endpoint's numbers.
"""

from django.contrib.auth import get_user_model
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.activities.models import Activity, ActivityType
from apps.exercises.models import Equipment, Exercise, ExerciseImage, MuscleGroup
from apps.measurements.models import BodyMeasurement, MeasurementType
from apps.nutrition.models import (
    DiaryEntry,
    DietPlan,
    DietPlanItem,
    DietPlanMeal,
    Food,
    GoalType,
    MealSlot,
    NutritionGoal,
    NutritionProfile,
    NutritionTarget,
    Recipe,
    RecipeIngredient,
)
from apps.programs.models import ExercisePrescription, Program, Workout
from apps.records.models import PersonalRecord
from apps.stretching.models import (
    PerformedStretch,
    RoutineItem,
    Stretch,
    StretchRoutine,
    StretchSession,
)
from apps.workouts import services as workout_services
from apps.workouts.models import ExerciseSet, PerformedExercise, WorkoutSession

User = get_user_model()

def display_language(request):
    """The language a display name is shown in: the request's
    Accept-Language when this server supports it, else the user's own
    profile language, else the site default. Worked out once per
    request."""
    from django.conf import settings
    from django.utils.translation import get_supported_language_variant
    from django.utils.translation.trans_real import parse_accept_lang_header

    if request is None:
        return None
    cached = getattr(request, "_display_language", None)
    if cached is not None:
        return cached
    language = None
    for code, _quality in parse_accept_lang_header(request.headers.get("Accept-Language", "")):
        if code == "*":
            break
        try:
            language = get_supported_language_variant(code)
            break
        except LookupError:
            continue
    user = getattr(request, "user", None)
    language = language or getattr(user, "language", "") or settings.LANGUAGE_CODE
    request._display_language = language
    return language


@extend_schema_field(OpenApiTypes.STR)
class DisplayNameField(serializers.ReadOnlyField):
    """`name` (or another `source`) as the web shows it (issues #36 and
    #42): built-in names and texts are stored in English and translated
    where they're rendered (`{% trans %}`,
    apps.core.templatetags.core_extras.translate_content), so a client
    gets the same text without shipping its own translations. Plain
    gettext, like translate_content - a text nobody translated, such as
    one a user typed, comes back unchanged. Used for the `display_*`
    twins next to the stored values: `display_name`,
    `exercise_display_name`, `display_instructions` and so on."""

    def __init__(self, **kwargs):
        kwargs.setdefault("source", "name")
        super().__init__(**kwargs)

    def to_representation(self, value):
        from django.utils import translation

        if not value:
            return value
        language = display_language(self.context.get("request"))
        if language is None:
            return translation.gettext(value)
        with translation.override(language):
            return translation.gettext(value)


# --------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------


class ProfileSerializer(serializers.ModelSerializer):
    # The web dashboard's "Time to log your body measurements?" rule
    # (apps.measurements.services.needs_body_tracking_reminder, issue
    # #40), so a client shows the same nudge without reimplementing it.
    # Just a yes/no - no reading is exposed through the profile context.
    needs_body_tracking_reminder = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "date_joined",
            "unit_system",
            "timezone",
            "height",
            "show_bmi",
            "show_achievements",
            "allow_friend_requests",
            "allow_group_invites",
            "language",
            "theme",
            "appearance",
            "body_tracking_reminders_enabled",
            "needs_body_tracking_reminder",
            "tutorials_enabled",
        ]
        # email too: an API key is a narrower credential than the
        # password, and changing the email would let whoever holds the
        # key request a password reset to themselves - a full account
        # takeover. Change it on the Account details page instead.
        read_only_fields = ["username", "email", "date_joined"]

    def get_needs_body_tracking_reminder(self, user) -> bool:
        from apps.measurements import services as measurement_services

        return measurement_services.needs_body_tracking_reminder(user)


# --------------------------------------------------------------------
# Exercises
# --------------------------------------------------------------------


class MuscleGroupSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    class Meta:
        model = MuscleGroup
        fields = ["id", "name", "display_name"]


class EquipmentSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    class Meta:
        model = Equipment
        fields = ["id", "name", "display_name"]


class ExerciseImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExerciseImage
        fields = ["id", "image", "caption", "attribution", "order"]


class ExerciseSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    display_instructions = DisplayNameField(source="instructions")
    is_custom = serializers.BooleanField(read_only=True)
    # Read-only, same pattern as e.g. DietPlanMealSerializer.items below -
    # a client manages its own exercise's gallery through the same
    # instructions/description fields it already writes, not through this
    # nested list; there's no apps.api endpoint for creating/deleting a
    # single ExerciseImage (the web app's own upload flow - apps.
    # exercises.views.exercise_image_create - handles multipart image
    # bytes directly, which this JSON API has no equivalent for yet).
    images = ExerciseImageSerializer(many=True, read_only=True)

    class Meta:
        model = Exercise
        fields = [
            "id",
            "name",
            "display_name",
            "description",
            "instructions",
            "display_instructions",
            "instructions_attribution",
            "images",
            "primary_muscle_groups",
            "secondary_muscle_groups",
            "equipment",
            "movement_type",
            "weight_input_mode",
            "active",
            "is_custom",
            "owner",
        ]
        read_only_fields = ["active", "owner", "instructions_attribution"]


# --------------------------------------------------------------------
# Programs (also covers Workout / ExercisePrescription - same context)
# --------------------------------------------------------------------


class ExercisePrescriptionSerializer(serializers.ModelSerializer):
    # Read-only, so a client can render a workout without fetching
    # every exercise separately (issue #19) - same field
    # PerformedExerciseSerializer already has.
    exercise_name = serializers.CharField(source="exercise.name", read_only=True)
    exercise_display_name = DisplayNameField(source="exercise.name")

    class Meta:
        model = ExercisePrescription
        fields = [
            "id",
            "workout",
            "exercise",
            "exercise_name",
            "exercise_display_name",
            "order",
            "set_count",
            "min_reps",
            "max_reps",
            "target_weight",
            "target_rpe",
            "target_rir",
            "progression_method",
            "weight_increment",
            "percentage_target",
            "notes",
        ]

    def validate(self, attrs):
        # ExercisePrescription.clean() enforces this same rule for the
        # web ModelForm path - DRF's ModelSerializer doesn't call a
        # model's clean() automatically, so it's repeated here rather
        # than silently only half-enforced depending on which door a
        # request came through.
        min_reps = attrs.get("min_reps", getattr(self.instance, "min_reps", None))
        max_reps = attrs.get("max_reps", getattr(self.instance, "max_reps", None))
        if min_reps and max_reps and min_reps > max_reps:
            raise serializers.ValidationError(
                {"min_reps": "Minimum reps cannot exceed maximum reps."}
            )
        return attrs

    def validate_workout(self, value):
        # A prescription can only ever be added to a workout this user
        # can actually edit (their own program, never a system
        # template) - apps.api.views.ExercisePrescriptionViewSet's own
        # get_queryset makes the same check for update/destroy, but
        # create has no existing instance for a queryset to scope, so
        # it's enforced here instead.
        from apps.programs import services as program_services

        request = self.context["request"]
        if not program_services.editable_by(request.user).filter(pk=value.program_id).exists():
            raise serializers.ValidationError("Not a workout you can edit.")
        return value

    def validate_exercise(self, value):
        # Regression found live: a plain ModelSerializer's
        # PrimaryKeyRelatedField has no ownership concept of its own
        # (same reasoning as BodyMeasurementSerializer.
        # validate_measurement_type's own comment) - without this, a
        # request could attach *any* exercise id, including someone
        # else's private custom exercise this user was never meant to
        # see at all, to their own prescription - and the response
        # would then echo that exercise's name back (`exercise_name`
        # above). The web UI trusts that
        # every prescription it renders already passed this check -
        # confirmed live, it happily prints a private exercise's name
        # on the *attacker's own* program page once such a row exists.
        from apps.exercises import services as exercise_services

        request = self.context["request"]
        if not exercise_services.visible_to(request.user).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not an exercise you can use.")
        return value


class WorkoutSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    prescriptions = ExercisePrescriptionSerializer(many=True, read_only=True)

    class Meta:
        model = Workout
        fields = [
            "id", "program", "name", "display_name", "order", "scheduled_weekday", "notes",
            "prescriptions",
        ]

    def validate_program(self, value):
        # Same reasoning as ExercisePrescriptionSerializer.validate_workout
        # above - a workout can only be added to a program this user
        # actually owns.
        from apps.programs import services as program_services

        request = self.context["request"]
        if not program_services.editable_by(request.user).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not a program you can edit.")
        return value


class ProgramSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    display_description = DisplayNameField(source="description")
    workouts = WorkoutSerializer(many=True, read_only=True)
    is_system_template = serializers.BooleanField(read_only=True)

    class Meta:
        model = Program
        fields = [
            "id",
            "name",
            "display_name",
            "description",
            "display_description",
            "is_continuous",
            "is_template",
            "version",
            "is_system_template",
            "owner",
            "workouts",
        ]
        read_only_fields = ["version", "owner"]


# --------------------------------------------------------------------
# Workouts (session logging)
# --------------------------------------------------------------------


class ExerciseSetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExerciseSet
        fields = [
            "id",
            "performed_exercise",
            "set_number",
            "weight",
            "reps",
            "target_reps",
            "rpe",
            "rir",
            "is_failure",
            "is_warmup",
            "notes",
            "performed_at",
        ]
        read_only_fields = ["set_number"]  # auto-numbered - see apps.workouts.services.log_set

    def to_representation(self, instance):
        # ExerciseSetViewSet.perform_create puts the records the set
        # broke here (issue #30), so only a create response carries
        # `new_records`; reading a set later doesn't.
        data = super().to_representation(instance)
        if "new_records" in self.context:
            data["new_records"] = NewRecordSerializer(
                self.context["new_records"], many=True, context=self.context
            ).data
        return data

    def validate_rpe(self, value):
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            workout_services.validate_rpe(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from None
        return value

    def validate_performed_exercise(self, value):
        request = self.context["request"]
        if value.session.user_id != request.user.id:
            raise serializers.ValidationError("Not your workout session.")
        # Only a brand-new set requires the session still be in
        # progress - editing an existing one doesn't (apps.workouts
        # .views.set_edit has no such check either).
        if self.instance is None and not value.session.is_in_progress:
            raise serializers.ValidationError("This session is no longer in progress.")
        return value


class PerformedExerciseSerializer(serializers.ModelSerializer):
    sets = ExerciseSetSerializer(many=True, read_only=True)
    # The name as it was when logged (PerformedExercise.exercise_name).
    exercise_name = serializers.CharField(source="display_name", read_only=True)
    exercise_display_name = DisplayNameField(source="display_name")

    class Meta:
        model = PerformedExercise
        fields = [
            "id",
            "session",
            "exercise",
            "exercise_name",
            "exercise_display_name",
            "order",
            "set_count",
            "min_reps",
            "max_reps",
            "target_weight",
            "target_rpe",
            "target_rir",
            "progression_method",
            "weight_increment",
            "notes",
            "sets",
        ]
        read_only_fields = [
            # All snapshotted at session-start (apps.workouts.services
            # .start_session) from the plan's ExercisePrescription - an
            # API caller adds exercises and logs sets against them, the
            # same as the web UI, but never edits the snapshot itself.
            "set_count",
            "min_reps",
            "max_reps",
            "target_weight",
            "target_rpe",
            "target_rir",
            "progression_method",
            "weight_increment",
        ]

    def validate_session(self, value):
        from apps.workouts.models import WorkoutSessionStatus

        request = self.context["request"]
        if value.user_id != request.user.id:
            raise serializers.ValidationError("Not your workout session.")
        if value.status != WorkoutSessionStatus.IN_PROGRESS:
            raise serializers.ValidationError("This session is no longer in progress.")
        return value

    def validate_exercise(self, value):
        # Same regression as ExercisePrescriptionSerializer.validate_
        # exercise's own comment, same fix - going off-plan mid-session
        # (apps.workouts.services.add_performed_exercise) is exactly as
        # exploitable as adding a prescription is.
        from apps.exercises import services as exercise_services

        request = self.context["request"]
        if not exercise_services.visible_to(request.user).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not an exercise you can use.")
        return value


class WorkoutSessionSerializer(serializers.ModelSerializer):
    performed_exercises = PerformedExerciseSerializer(many=True, read_only=True)
    is_in_progress = serializers.BooleanField(read_only=True)

    class Meta:
        model = WorkoutSession
        fields = [
            "id",
            "program",
            "workout",
            "status",
            "started_at",
            "ended_at",
            "is_in_progress",
            "performed_exercises",
        ]
        read_only_fields = ["program", "started_at", "ended_at"]

    def validate_workout(self, value):
        # Matches apps.workouts.views.session_start's own
        # get_object_or_404(Workout, ..., program__in=visible_to(user))
        # - starting a session from a workout you can't even see would
        # otherwise leak whether some other user's private workout id
        # exists at all.
        if value is None:
            return value
        from apps.programs import services as program_services

        request = self.context["request"]
        if not program_services.visible_to(request.user).filter(pk=value.program_id).exists():
            raise serializers.ValidationError("Not a workout you can start.")
        return value

    def validate_status(self, value):
        # Only checked on update - a create() carries whatever default
        # status a fresh session gets regardless of what's in the
        # payload (perform_create never reads it), so there's nothing
        # to validate there. On update, only the two "end a session"
        # transitions are ever valid (starting one goes through
        # create() instead, since it needs a workout/freeform choice,
        # not a bare status flip) - matches what the web UI's own
        # session-complete/session-abandon endpoints allow and nothing
        # more.
        if self.instance is None:
            return value
        allowed = {"completed", "abandoned"}
        if value not in allowed:
            raise serializers.ValidationError(f"status can only be set to one of {allowed}.")
        # A finished session is history - see
        # apps.workouts.services.SessionNotInProgress. Re-sending the
        # status it already has is a harmless no-op (perform_update
        # skips it); changing it is not.
        if not self.instance.is_in_progress and value != self.instance.status:
            raise serializers.ValidationError("This session is no longer in progress.")
        return value


# --------------------------------------------------------------------
# Measurements
# --------------------------------------------------------------------


class MeasurementTypeSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    is_custom = serializers.BooleanField(read_only=True)

    class Meta:
        model = MeasurementType
        fields = ["id", "name", "display_name", "unit_kind", "active", "is_custom", "owner"]
        read_only_fields = ["active", "owner"]


class BodyMeasurementSerializer(serializers.ModelSerializer):
    class Meta:
        model = BodyMeasurement
        fields = ["id", "measurement_type", "value", "recorded_at", "notes"]

    def validate_measurement_type(self, value):
        # A user may only log against a type they can actually see
        # (system types + their own) - mirrors
        # apps.measurements.services.visible_to, checked here since a
        # ModelSerializer's PrimaryKeyRelatedField has no ownership
        # concept of its own.
        from apps.measurements import services as measurement_services

        request = self.context["request"]
        if not measurement_services.visible_to(request.user).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not a measurement type you can log against.")
        return value


# --------------------------------------------------------------------
# Activities
# --------------------------------------------------------------------


class ActivityTypeSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    is_custom = serializers.BooleanField(read_only=True)

    class Meta:
        model = ActivityType
        fields = ["id", "name", "display_name", "active", "is_custom", "owner"]
        read_only_fields = ["active", "owner"]


class ActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Activity
        fields = [
            "id",
            "activity_type",
            "date",
            "start_time",
            "duration",
            "distance",
            "calories",
            "notes",
        ]

    def validate_activity_type(self, value):
        from apps.activities import services as activity_services

        request = self.context["request"]
        if not activity_services.visible_to(request.user).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not an activity type you can log against.")
        return value


# --------------------------------------------------------------------
# Records (read-only - PRs are derived, never directly writable)
# --------------------------------------------------------------------


class PersonalRecordSerializer(serializers.ModelSerializer):
    record_type_display = serializers.CharField(source="get_record_type_display", read_only=True)
    exercise_name = serializers.CharField(source="exercise.name", read_only=True)
    exercise_display_name = DisplayNameField(source="exercise.name")

    class Meta:
        model = PersonalRecord
        fields = [
            "id",
            "exercise",
            "exercise_name",
            "exercise_display_name",
            "record_type",
            "record_type_display",
            "rep_count",
            "value",
            "weight",
            "reps",
            "achieved_at",
        ]


class NewRecordSerializer(PersonalRecordSerializer):
    """A record a just-logged set broke, plus the value it beat
    (`previous_value`, null for a first-ever record) - what the web's
    PR toast shows. Only ever in a `sets/` create response."""

    previous_value = serializers.DecimalField(
        max_digits=8, decimal_places=2, read_only=True, allow_null=True, default=None
    )

    class Meta(PersonalRecordSerializer.Meta):
        fields = [*PersonalRecordSerializer.Meta.fields, "previous_value"]


class ExerciseSetCreatedSerializer(ExerciseSetSerializer):
    """Schema only - the `sets/` create response, documenting the
    `new_records` ExerciseSetSerializer.to_representation adds."""

    new_records = NewRecordSerializer(many=True, read_only=True)

    class Meta(ExerciseSetSerializer.Meta):
        fields = [*ExerciseSetSerializer.Meta.fields, "new_records"]


# --------------------------------------------------------------------
# Analytics (read-only aggregates - no backing model of their own)
# --------------------------------------------------------------------


class SuggestionSerializer(serializers.Serializer):
    """apps.progression.suggestions.Suggestion, plus the prescription's
    progression `method`. Every field is null for a performed exercise
    with no prescription behind it (added ad hoc), same as the web,
    which shows no suggestion then. `weight` is kg like everywhere else
    in this API; `reason` is in the user's own language."""

    weight = serializers.DecimalField(
        source="suggested_weight", max_digits=6, decimal_places=2, allow_null=True
    )
    min_reps = serializers.IntegerField(source="target_min_reps", allow_null=True)
    max_reps = serializers.IntegerField(source="target_max_reps", allow_null=True)
    method = serializers.CharField(allow_null=True)
    action = serializers.CharField(allow_null=True)
    confidence = serializers.CharField(allow_null=True)
    reason = serializers.CharField(allow_null=True)


class LastPerformanceSerializer(serializers.Serializer):
    """apps.workouts.services.last_performance - `session`/`started_at`
    null and `sets` empty when the exercise has never been completed."""

    session = serializers.IntegerField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    sets = ExerciseSetSerializer(many=True)


class UpNextProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = Program
        fields = ["id", "name", "is_continuous"]


class UpNextSerializer(serializers.Serializer):
    """apps.workouts.services.UpNext - every field null when there's
    nothing next (no program trained from yet), so the shape never
    changes."""

    program = UpNextProgramSerializer(allow_null=True)
    workout = WorkoutSerializer(allow_null=True)
    day = serializers.DateField(allow_null=True)


class TrainingSummarySerializer(serializers.Serializer):
    session_count = serializers.IntegerField()
    total_duration_seconds = serializers.SerializerMethodField()
    total_volume = serializers.DecimalField(max_digits=12, decimal_places=2)

    def get_total_duration_seconds(self, obj) -> float:
        return obj.total_duration.total_seconds()


class AnalyticsRangeSerializer(serializers.Serializer):
    """The resolved `?range=` - `start` is null for `all`."""

    range = serializers.CharField(source="key")
    start = serializers.DateField(allow_null=True)
    end = serializers.DateField()


class WeeklyVolumeSerializer(serializers.Serializer):
    week_start = serializers.DateField()
    volume = serializers.DecimalField(max_digits=14, decimal_places=2)


class VolumeAnalyticsSerializer(serializers.Serializer):
    """`analytics/volume/` - kg x reps of working sets, canonical kg."""

    range = AnalyticsRangeSerializer()
    weeks = WeeklyVolumeSerializer(many=True)


class MuscleGroupVolumeSerializer(serializers.Serializer):
    muscle_group = serializers.IntegerField(source="muscle_group.pk")
    muscle_group_name = serializers.CharField(source="muscle_group.name")
    muscle_group_display_name = DisplayNameField(source="muscle_group.name")
    volume = serializers.DecimalField(max_digits=14, decimal_places=2)


class MuscleGroupAnalyticsSerializer(serializers.Serializer):
    range = AnalyticsRangeSerializer()
    muscle_groups = MuscleGroupVolumeSerializer(many=True)


class SessionBestSerializer(serializers.Serializer):
    date = serializers.DateField()
    estimated_one_rep_max = serializers.DecimalField(max_digits=10, decimal_places=2)
    weight = serializers.DecimalField(max_digits=10, decimal_places=2)
    reps = serializers.IntegerField()


class ExerciseAnalyticsSerializer(serializers.Serializer):
    """`analytics/exercises/<id>/` - canonical kg throughout."""

    range = AnalyticsRangeSerializer()
    exercise = serializers.IntegerField(source="exercise.pk")
    session_count = serializers.IntegerField()
    total_volume = serializers.DecimalField(max_digits=14, decimal_places=2)
    sessions = SessionBestSerializer(many=True)


class RecentActivitySerializer(serializers.Serializer):
    """apps.analytics.achievements.RecentActivity - one opted-in user's
    latest training, the dashboard's "Recently active" list."""

    display_name = serializers.CharField()
    username = serializers.CharField()
    last_active_at = serializers.DateTimeField()
    is_in_progress = serializers.BooleanField()
    is_recent = serializers.BooleanField()


class AchievementSerializer(serializers.Serializer):
    icon = serializers.CharField()
    label = serializers.CharField()
    value = serializers.CharField()
    # Matches apps.analytics.achievements.Achievement's own field name
    # (renamed from `username`) - this can be "alice (Alice)", not
    # necessarily the bare username, once a user opts in via
    # User.show_name_to_others, so the API field is named for what it
    # actually contains.
    display_name = serializers.CharField()


# --------------------------------------------------------------------
# Nutrition
# --------------------------------------------------------------------


class FoodSerializer(serializers.ModelSerializer):
    class Meta:
        model = Food
        fields = [
            "id",
            "name",
            "brand",
            "serving_size",
            "serving_unit",
            "calories",
            "protein_grams",
            "carbohydrate_grams",
            "fat_grams",
            "fiber_grams",
            "sugar_grams",
            "saturated_fat_grams",
            "sodium_mg",
            "energy_kj",
            "starch_grams",
            "polyols_grams",
            "salt_grams",
            "other_nutrients",
            "nutri_score",
            "nova_group",
            "off_id",
            "active",
            "owner",
        ]
        # nutri_score/nova_group/off_id are only ever set by an
        # OpenFoodFacts import (apps.nutrition.services.import_or_
        # refresh_food_from_off, `POST foods/import-off/`), never
        # hand-entered - same reasoning as docs/NUTRITION.md's own
        # "Food" section.
        read_only_fields = [
            "other_nutrients", "nutri_score", "nova_group", "off_id", "active", "owner",
        ]


class FoodImportOffSerializer(serializers.Serializer):
    barcode = serializers.RegexField(r"^\d{8,14}$", help_text="8-14 digits.")


class OffSearchResultSerializer(serializers.Serializer):
    """One Open Food Facts product not imported yet - per 100 g/ml.
    `POST foods/import-off/` with its `off_id` saves it as a food."""

    off_id = serializers.CharField()
    name = serializers.CharField()
    brand = serializers.CharField(allow_blank=True)
    serving_size = serializers.DecimalField(max_digits=10, decimal_places=2)
    serving_unit = serializers.CharField()
    calories = serializers.IntegerField()
    protein_grams = serializers.DecimalField(max_digits=10, decimal_places=2)
    carbohydrate_grams = serializers.DecimalField(max_digits=10, decimal_places=2)
    fat_grams = serializers.DecimalField(max_digits=10, decimal_places=2)
    nutri_score = serializers.CharField(allow_blank=True, allow_null=True)
    nova_group = serializers.IntegerField(allow_null=True)
    image_thumb_url = serializers.CharField(allow_blank=True)


class OffSearchSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "disabled", "busy", "error"])
    local = FoodSerializer(many=True)
    results = OffSearchResultSerializer(many=True)


class MealSlotSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    is_custom = serializers.BooleanField(read_only=True)

    class Meta:
        model = MealSlot
        fields = ["id", "name", "display_name", "order", "active", "is_custom", "owner"]
        read_only_fields = ["active", "owner"]


class RecipeIngredientSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeIngredient
        fields = ["id", "recipe", "food", "quantity", "order"]

    def validate_recipe(self, value):
        # Same reasoning as ExercisePrescriptionSerializer.validate_
        # workout - an ingredient can only be added to a recipe this
        # user actually owns.
        request = self.context["request"]
        if value.owner_id != request.user.id or not value.active:
            raise serializers.ValidationError("Not a recipe you own.")
        return value

    def validate_food(self, value):
        from django.db.models import Q

        request = self.context["request"]
        visible = Food.objects.filter(
            Q(owner=request.user) | Q(owner__isnull=True), active=True
        )
        if not visible.filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not a food you can use.")
        return value


class RecipeSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    display_instructions = DisplayNameField(source="instructions")
    ingredients = RecipeIngredientSerializer(many=True, read_only=True)

    class Meta:
        model = Recipe
        fields = [
            "id", "name", "display_name", "servings", "instructions", "display_instructions",
            "owner", "ingredients",
        ]
        read_only_fields = ["owner"]


class NutritionValuesSerializer(serializers.Serializer):
    """apps.nutrition.services.ScaledNutrition - one amount's nutrition,
    already scaled. The optional nutrients are null when unknown."""

    calories = serializers.DecimalField(max_digits=12, decimal_places=2)
    protein_grams = serializers.DecimalField(max_digits=12, decimal_places=2)
    carbohydrate_grams = serializers.DecimalField(max_digits=12, decimal_places=2)
    fat_grams = serializers.DecimalField(max_digits=12, decimal_places=2)
    fiber_grams = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    sugar_grams = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    saturated_fat_grams = serializers.DecimalField(
        max_digits=12, decimal_places=2, allow_null=True
    )
    sodium_mg = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)


class DiaryEntrySerializer(serializers.ModelSerializer):
    # The entry's nutrition for its quantity, from the values snapshotted
    # when it was logged (services.diary_entry_nutrition) - the same
    # figures the web diary adds up, even after the food or recipe was
    # edited or deleted (issue #27). The names are there for the same
    # reason: a deleted recipe can't be fetched from recipes/.
    nutrition = serializers.SerializerMethodField()
    food_name = serializers.CharField(source="food.name", read_only=True, default=None)
    recipe_name = serializers.CharField(source="recipe.name", read_only=True, default=None)
    recipe_display_name = DisplayNameField(source="recipe.name", default=None)

    class Meta:
        model = DiaryEntry
        fields = [
            "id", "date", "meal_slot", "food", "food_name", "recipe", "recipe_name",
            "recipe_display_name",
            "quantity", "notes", "user",
            "quick_name", "quick_calories", "quick_protein_grams",
            "quick_carbohydrate_grams", "quick_fat_grams", "nutrition",
        ]
        read_only_fields = ["user"]

    @extend_schema_field(NutritionValuesSerializer)
    def get_nutrition(self, entry):
        from apps.nutrition import services as nutrition_services

        return NutritionValuesSerializer(nutrition_services.diary_entry_nutrition(entry)).data

    def validate(self, attrs):
        # Mirrors DiaryEntry.clean() / its own CheckConstraint -
        # checked here too so a bad request gets a normal 400 with a
        # clear message instead of a raw IntegrityError 500 from the
        # database constraint alone.
        def current(name):
            return attrs.get(name, getattr(self.instance, name, None))

        kinds = [bool(current("food")), bool(current("recipe")),
                 current("quick_calories") is not None]
        if sum(kinds) != 1:
            raise serializers.ValidationError(
                "Log either a food, a recipe or quick macros (quick_calories) - exactly one."
            )
        return attrs

    def validate_meal_slot(self, value):
        from apps.nutrition import services as nutrition_services

        request = self.context["request"]
        if not nutrition_services.visible_meal_slots(request.user).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not a meal slot you can log against.")
        return value

    # food and recipe are both nullable: an explicit null (issue #39)
    # is how a client says "not this kind of entry", and validate()
    # above already checks that exactly one kind is given.
    def validate_food(self, value):
        from django.db.models import Q

        if value is None:
            return value
        if self.instance is not None and value.pk == self.instance.food_id:
            return value  # unchanged - see validate_recipe
        request = self.context["request"]
        visible = Food.objects.filter(
            Q(owner=request.user) | Q(owner__isnull=True), active=True
        )
        if not visible.filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not a food you can log.")
        return value

    def validate_recipe(self, value):
        if value is None:
            return value
        if self.instance is not None and value.pk == self.instance.recipe_id:
            # Unchanged - fine even if the recipe has since been deleted
            # (hidden), so a client can resend the whole entry when only
            # editing its quantity, as the web's diary edit does.
            return value
        # The user's own or a built-in template recipe - logging a
        # template to your own diary is what it's there for, same as
        # the web's recipe_log (issue #38).
        request = self.context["request"]
        if value.owner_id not in (request.user.id, None) or not value.active:
            raise serializers.ValidationError("Not a recipe you can log.")
        return value


class DiaryCopyDaySerializer(serializers.Serializer):
    source_date = serializers.DateField()
    target_date = serializers.DateField()


class DiaryCopyDayResultSerializer(serializers.Serializer):
    copied = serializers.IntegerField()


class DailyTotalsSerializer(serializers.Serializer):
    date = serializers.DateField()
    totals = NutritionValuesSerializer()


# Goals/targets are historized and only ever change through
# apps.nutrition.services.set_goal/set_target (append a new row,
# close the old one) - exposed read-only here for exactly the same
# reason PersonalRecordSerializer is read-only above: a raw PATCH
# through a generic serializer could silently rewrite history a
# derived/historized resource must never allow. A new goal is created
# through services.change_goal instead (NutritionGoalCreateSerializer),
# and a target only ever follows from a goal or an accepted adjustment.
class NutritionGoalSerializer(serializers.ModelSerializer):
    class Meta:
        model = NutritionGoal
        fields = [
            "id",
            "goal_type",
            "target_weight",
            "target_rate_kg_per_week",
            "started_at",
            "ended_at",
            "notes",
        ]


class NutritionGoalCreateSerializer(serializers.Serializer):
    """`POST nutrition-goals/` - a new goal, through services.change_goal
    exactly like the web's goal page: the open goal and target are
    ended (kept in history) and a target is calculated for the new one.
    `target_weight` is canonical kg, like every weight in this API."""

    goal_type = serializers.ChoiceField(choices=GoalType.choices)
    target_rate_kg_per_week = serializers.DecimalField(max_digits=5, decimal_places=3)
    target_weight = serializers.DecimalField(
        max_digits=8, decimal_places=2, min_value=20, required=False, allow_null=True
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        from apps.nutrition import services as nutrition_services

        error = nutrition_services.goal_rate_error(
            attrs["goal_type"], attrs["target_rate_kg_per_week"]
        )
        if error:
            raise serializers.ValidationError({"target_rate_kg_per_week": error})
        return attrs


class AdjustmentSuggestionSerializer(serializers.Serializer):
    """apps.nutrition.suggestions.AdjustmentSuggestion - the nutrition
    dashboard's calorie adjustment card. Rates in kg/week; `action` is
    `adjust` only when there's something to accept."""

    action = serializers.CharField()
    target_rate_kg_per_week = serializers.DecimalField(
        max_digits=6, decimal_places=3, allow_null=True
    )
    actual_rate_kg_per_week = serializers.DecimalField(
        max_digits=6, decimal_places=3, allow_null=True
    )
    suggested_daily_calories = serializers.IntegerField(allow_null=True)
    delta_kcal = serializers.IntegerField(allow_null=True)
    confidence = serializers.CharField(allow_null=True)
    reason = serializers.CharField()


class MaintenanceEstimateSerializer(serializers.Serializer):
    """apps.nutrition.maintenance.MaintenanceEstimate - the nutrition
    dashboard's "Observed maintenance" card: what the user's intake and
    weight trend over the last 28 finished days say their real
    maintenance is. `calories`/`confidence` are null when there isn't
    enough data, and `reason` says what's missing. The figures behind
    it are null until the estimate got that far: the food diary's
    (`days_logged`, `mean_calories`, `possible_logging_gaps`), then the
    weight trend's (`weight_rate_kg_per_week`, `weight_days`,
    `span_days`). Information only - it never changes a target."""

    calories = serializers.IntegerField(allow_null=True)
    confidence = serializers.CharField(allow_null=True)
    reason = serializers.CharField()
    window_days = serializers.IntegerField()
    days_logged = serializers.IntegerField(allow_null=True)
    mean_calories = serializers.DecimalField(max_digits=8, decimal_places=0, allow_null=True)
    possible_logging_gaps = serializers.BooleanField(allow_null=True)
    weight_rate_kg_per_week = serializers.DecimalField(
        max_digits=6, decimal_places=3, allow_null=True
    )
    weight_days = serializers.IntegerField(allow_null=True)
    span_days = serializers.IntegerField(allow_null=True)


class NutritionStatsSummarySerializer(serializers.Serializer):
    """apps.nutrition.services.NutritionStatsSummary - only days with
    something logged count; `possible_logging_gaps` is true when the mean
    is low enough against the median to suggest partly logged days."""

    days_logged = serializers.IntegerField()
    days_in_range = serializers.IntegerField()
    mean_calories = serializers.DecimalField(max_digits=8, decimal_places=0)
    median_calories = serializers.DecimalField(max_digits=8, decimal_places=0)
    median_protein_grams = serializers.DecimalField(max_digits=8, decimal_places=1)
    median_carbohydrate_grams = serializers.DecimalField(max_digits=8, decimal_places=1)
    median_fat_grams = serializers.DecimalField(max_digits=8, decimal_places=1)
    possible_logging_gaps = serializers.BooleanField()


class NutritionStatsPeriodSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    start = serializers.DateField()
    end = serializers.DateField()
    summary = NutritionStatsSummarySerializer()


class CalorieDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    calories = serializers.IntegerField(allow_null=True)


class NutritionStatsSerializer(serializers.Serializer):
    """`GET nutrition/stats/?range=` - the nutrition statistics page.
    `period` is the chosen range's figures (every period ends yesterday,
    the last finished day); `daily` has one row per day from its start
    through today for the chart, `calories` null on a day with nothing
    logged; `target_calories` is the active target, the chart's
    reference line (null without one); `periods` is every fixed period,
    the page's table."""

    period = NutritionStatsPeriodSerializer()
    daily = CalorieDaySerializer(many=True)
    target_calories = serializers.IntegerField(allow_null=True)
    periods = NutritionStatsPeriodSerializer(many=True)


class NutritionTargetSerializer(serializers.ModelSerializer):
    # NutritionTarget.display_reason under the API's existing "reason"
    # key - re-rendered in the request's active language when the
    # structured snapshot is available, same as the dashboard, rather
    # than exposing the raw model field that can be frozen in whatever
    # language was active when the row was created. See
    # NutritionTarget.display_reason's own docstring.
    reason = serializers.CharField(source="display_reason", read_only=True)

    class Meta:
        model = NutritionTarget
        fields = [
            "id",
            "goal",
            "daily_calories",
            "protein_grams",
            "carbohydrate_grams",
            "fat_grams",
            "source",
            "reason",
            "started_at",
            "ended_at",
        ]


class NutritionGoalCreatedSerializer(NutritionGoalSerializer):
    """The `POST nutrition-goals/` response: the goal and the target
    calculated for it."""

    target = NutritionTargetSerializer(read_only=True)

    class Meta(NutritionGoalSerializer.Meta):
        fields = [*NutritionGoalSerializer.Meta.fields, "target"]


class NutritionProfileSerializer(serializers.ModelSerializer):
    # A property, not a model field (NutritionProfile.age_years,
    # derived from birth_date so it never goes stale) - exposed
    # read-only the same way DiaryEntrySerializer doesn't re-derive
    # anything the model already computes for it.
    age_years = serializers.IntegerField(read_only=True)

    class Meta:
        model = NutritionProfile
        fields = [
            "biological_sex",
            "birth_date",
            "age_years",
            "activity_job",
            "daily_steps",
            "training_sessions_per_week",
            "training_session_minutes",
            "other_exercise_minutes_per_week",
            "activity_level",
            "self_reported_daily_calories",
        ]


class DietPlanItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = DietPlanItem
        fields = ["id", "food", "recipe", "quantity", "order"]


class DietPlanMealSerializer(serializers.ModelSerializer):
    items = DietPlanItemSerializer(many=True, read_only=True)

    class Meta:
        model = DietPlanMeal
        fields = ["id", "meal_slot", "target_calories", "order", "weekday", "items"]


class DietPlanSerializer(serializers.ModelSerializer):
    """`meals`/each meal's `items` are read-only here - they're always
    generated by apps.nutrition.diet_builder.build_diet_plan's own
    calorie-splitting algorithm (DietPlanViewSet.perform_create below),
    never hand-inserted through this serializer, the same "a raw write
    could silently corrupt what a domain service guarantees" reasoning
    NutritionGoal/NutritionTarget above already follow - unlike a
    recipe's own hand-edited ingredient list, a diet plan's meals/items
    are meant to stay whatever the wizard actually computed.
    `is_active` is a real Meta.read_only_fields entry - it's never
    valid input, on create or update alike. The four `target_*` fields
    aren't: DietPlanViewSet.perform_create needs them as writable input
    to pass through to `build_diet_plan`, so they can't be marked
    read-only in Meta (DRF strips a read-only field from
    validated_data unconditionally, create included). Instead
    perform_update discards them the same way it already discards
    `meal_slots` - on update only, they're a snapshot of what the plan
    was actually built against (DietPlan's own model docstring), and
    only `build_diet_plan` should ever set them; a raw PATCH changing
    them would silently disconnect a plan's stated targets from what
    its `meals` were actually generated to hit.

    `meal_slots` isn't a real DietPlan field at all - write-only input
    for `build_diet_plan`'s own `meal_slots` kwarg, only meaningful on
    create (DietPlanViewSet.perform_update discards it from an update).
    """

    meals = DietPlanMealSerializer(many=True, read_only=True)
    meal_slots = serializers.PrimaryKeyRelatedField(
        many=True, write_only=True, queryset=MealSlot.objects.all()
    )

    class Meta:
        model = DietPlan
        fields = [
            "id",
            "name",
            "goal",
            "target_calories",
            "target_protein_grams",
            "target_carbohydrate_grams",
            "target_fat_grams",
            "is_active",
            "is_weekly",
            "meals",
            "meal_slots",
        ]
        read_only_fields = ["is_active"]

    def validate_meal_slots(self, value):
        from apps.nutrition import services as nutrition_services

        request = self.context["request"]
        visible_ids = set(
            nutrition_services.visible_meal_slots(request.user).values_list("id", flat=True)
        )
        if any(slot.pk not in visible_ids for slot in value):
            raise serializers.ValidationError("Not a meal slot you can use.")
        return value

    def validate_goal(self, value):
        request = self.context["request"]
        if value is not None and value.user_id != request.user.id:
            raise serializers.ValidationError("Not your own goal.")
        return value


# --------------------------------------------------------------------
# Stretching
# --------------------------------------------------------------------


class StretchSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    display_instructions = DisplayNameField(source="instructions")
    is_custom = serializers.BooleanField(read_only=True)

    class Meta:
        model = Stretch
        fields = [
            "id",
            "name",
            "display_name",
            "kind",
            "per_side",
            "default_hold_seconds",
            "muscle_groups",
            "instructions",
            "display_instructions",
            "active",
            "is_custom",
            "owner",
        ]
        read_only_fields = ["active", "owner"]


class RoutineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoutineItem
        fields = ["id", "routine", "stretch", "order", "hold_seconds", "sets", "rest_seconds"]

    def validate_routine(self, value):
        # Same reasoning as RecipeIngredientSerializer.validate_recipe -
        # only a routine this user owns can be edited.
        if value.owner_id != self.context["request"].user.id:
            raise serializers.ValidationError("Not a routine you own.")
        return value

    def validate_stretch(self, value):
        from apps.stretching import services as stretching_services

        if not stretching_services.visible_stretches(self.context["request"].user).filter(
            pk=value.pk
        ).exists():
            raise serializers.ValidationError("Not a stretch you can use.")
        return value


class StretchRoutineSerializer(serializers.ModelSerializer):
    display_name = DisplayNameField()
    display_description = DisplayNameField(source="description")
    items = RoutineItemSerializer(many=True, read_only=True)
    is_custom = serializers.BooleanField(read_only=True)

    class Meta:
        model = StretchRoutine
        fields = [
            "id", "name", "display_name", "description", "display_description", "items",
            "active", "is_custom", "owner",
        ]
        read_only_fields = ["active", "owner"]


class PerformedStretchSerializer(serializers.ModelSerializer):
    stretch_display_name = DisplayNameField(source="stretch_name")

    class Meta:
        model = PerformedStretch
        fields = [
            "id",
            "stretch",
            "stretch_name",
            "stretch_display_name",
            "order",
            "per_side",
            "target_hold_seconds",
            "sets",
            "rest_seconds",
            "status",
            "actual_seconds",
        ]


def _validate_after_workout(serializer, value):
    """A cool-down belongs to one of the user's own finished workouts,
    as on the web (stretching.views.session_start_cooldown)."""
    from apps.workouts.models import WorkoutSessionStatus

    if value is None:
        return value
    if value.user_id != serializer.context["request"].user.id or (
        value.status != WorkoutSessionStatus.COMPLETED
    ):
        raise serializers.ValidationError("Not a finished workout of yours.")
    return value


class StretchSessionSerializer(serializers.ModelSerializer):
    """Creating one here is a quick log - a finished session with a
    duration (services.quick_log); a guided session starts with
    `stretch-sessions/start/` instead. `after_workout` can be given on
    create to mark it as that workout's cool-down. Everything
    snapshotted at start (name, routine, per-stretch plan, the workout
    it followed) is read-only afterwards, same as the web edit form:
    date, duration and notes stay editable."""

    performed_stretches = PerformedStretchSerializer(many=True, read_only=True)

    class Meta:
        model = StretchSession
        fields = [
            "id",
            "name",
            "routine",
            "after_workout",
            "status",
            "date",
            "started_at",
            "completed_at",
            "duration",
            "notes",
            "performed_stretches",
        ]
        read_only_fields = ["name", "after_workout", "status", "started_at", "completed_at"]
        extra_kwargs = {"duration": {"required": True, "allow_null": False}}


class StretchSessionQuickLogSerializer(StretchSessionSerializer):
    """The quick-log create's input: StretchSessionSerializer plus a
    writable `after_workout`. A separate class so `after_workout` stays a
    required, read-only property of every StretchSession response in the
    schema - making it writable there would make it optional, a breaking
    change for v1 clients."""

    after_workout = serializers.PrimaryKeyRelatedField(
        queryset=WorkoutSession.objects.all(), required=False, allow_null=True
    )

    def validate_after_workout(self, value):
        return _validate_after_workout(self, value)

    def validate_routine(self, value):
        from apps.stretching import services as stretching_services

        if value is None:
            return value
        if self.instance is not None and value != self.instance.routine:
            raise serializers.ValidationError("A logged session's routine can't be changed.")
        if not stretching_services.visible_routines(self.context["request"].user).filter(
            pk=value.pk
        ).exists():
            raise serializers.ValidationError("Not a routine you can use.")
        return value


class CooldownSuggestionSerializer(serializers.Serializer):
    """`GET sessions/<id>/cooldown/` - what to stretch after a finished
    workout (stretching.services.suggest_cooldown, the web's cool-down
    card): the trained primary muscle groups, then either a `routine`
    that covers them or up to six library `stretches` (one of the two,
    both empty when nothing matches), and `planned_seconds` for it.
    `done` is the cool-down already started or done after this workout,
    if any; `POST stretch-sessions/start/` with `after_workout` starts
    one."""

    muscle_groups = MuscleGroupSerializer(many=True)
    routine = StretchRoutineSerializer(allow_null=True)
    stretches = StretchSerializer(many=True)
    planned_seconds = serializers.IntegerField(allow_null=True)
    done = StretchSessionSerializer(allow_null=True)


class StretchSessionStartSerializer(serializers.Serializer):
    """`POST stretch-sessions/start/` - a guided session from a routine,
    or from a plain list of stretches at their default holds (e.g. a
    cool-down), exactly one of the two."""

    routine = serializers.PrimaryKeyRelatedField(
        queryset=StretchRoutine.objects.all(), required=False, allow_null=True
    )
    stretches = serializers.PrimaryKeyRelatedField(
        queryset=Stretch.objects.all(), many=True, required=False
    )
    name = serializers.CharField(required=False, allow_blank=True, default="")
    after_workout = serializers.PrimaryKeyRelatedField(
        queryset=WorkoutSession.objects.all(), required=False, allow_null=True
    )

    def validate_routine(self, value):
        from apps.stretching import services as stretching_services

        if value is None:
            return value
        user = self.context["request"].user
        if not stretching_services.visible_routines(user).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not a routine you can use.")
        if not value.items.exists():
            raise serializers.ValidationError("This routine has no stretches yet.")
        return value

    def validate_stretches(self, value):
        from apps.stretching import services as stretching_services

        visible = set(
            stretching_services.visible_stretches(self.context["request"].user).values_list(
                "pk", flat=True
            )
        )
        if any(stretch.pk not in visible for stretch in value):
            raise serializers.ValidationError("Not a stretch you can use.")
        return value

    def validate_after_workout(self, value):
        return _validate_after_workout(self, value)

    def validate(self, attrs):
        if bool(attrs.get("routine")) == bool(attrs.get("stretches")):
            raise serializers.ValidationError("Give either a routine or stretches - exactly one.")
        return attrs


class PerformedStretchMarkSerializer(serializers.Serializer):
    """One stretch of a guided session done (optionally with the
    seconds actually held) or skipped - services.mark_performed."""

    performed_stretch = serializers.IntegerField()
    done = serializers.BooleanField()
    actual_seconds = serializers.IntegerField(min_value=0, required=False, allow_null=True)


# --------------------------------------------------------------------
# Shopping list (apps.nutrition.shopping) - derived, never stored
# --------------------------------------------------------------------


class ShoppingSettingsSerializer(serializers.Serializer):
    """A diet plan's shopping settings: the weekdays the user shops
    (0=Monday .. 6=Sunday) and whether a shopping day's own meals go on
    that day's list (`true`) or the previous one (`false`)."""

    weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6), allow_empty=True
    )
    include_shopping_day = serializers.BooleanField()


class ShoppingTripSerializer(serializers.Serializer):
    weekday = serializers.IntegerField(
        allow_null=True, help_text="The shopping day; null when no shopping days are set."
    )
    days = serializers.ListField(
        child=serializers.IntegerField(), help_text="Weekdays whose meals this trip buys for."
    )


class ShoppingDayAmountSerializer(serializers.Serializer):
    weekday = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)


class ShoppingItemSerializer(serializers.Serializer):
    food = serializers.IntegerField(source="food.pk")
    food_name = serializers.CharField(source="food.name")
    unit = serializers.CharField(help_text="The food's serving unit: g, ml or piece.")
    total = serializers.DecimalField(max_digits=10, decimal_places=2)
    by_day = ShoppingDayAmountSerializer(many=True)


class ShoppingListSerializer(serializers.Serializer):
    trip = ShoppingTripSerializer()
    trips = ShoppingTripSerializer(many=True)
    include_shopping_day = serializers.BooleanField()
    items = ShoppingItemSerializer(many=True)

