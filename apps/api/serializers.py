"""DRF serializers for every apps.api context — see docs/API.md.

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
from rest_framework import serializers

from apps.activities.models import Activity, ActivityType
from apps.exercises.models import Equipment, Exercise, MuscleGroup
from apps.measurements.models import BodyMeasurement, MeasurementType
from apps.nutrition.models import (
    DiaryEntry,
    DietPlan,
    DietPlanItem,
    DietPlanMeal,
    Food,
    MealSlot,
    NutritionGoal,
    NutritionProfile,
    NutritionTarget,
    Recipe,
    RecipeIngredient,
)
from apps.programs.models import ExercisePrescription, Program, Workout
from apps.records.models import PersonalRecord
from apps.workouts.models import ExerciseSet, PerformedExercise, WorkoutSession

User = get_user_model()

# --------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "unit_system",
            "timezone",
            "height",
            "show_bmi",
            "show_achievements",
            "allow_friend_requests",
            "allow_group_invites",
            "language",
        ]
        read_only_fields = ["username"]


# --------------------------------------------------------------------
# Exercises
# --------------------------------------------------------------------


class MuscleGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = MuscleGroup
        fields = ["id", "name"]


class EquipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Equipment
        fields = ["id", "name"]


class ExerciseSerializer(serializers.ModelSerializer):
    is_custom = serializers.BooleanField(read_only=True)

    class Meta:
        model = Exercise
        fields = [
            "id",
            "name",
            "description",
            "primary_muscle_groups",
            "secondary_muscle_groups",
            "equipment",
            "movement_type",
            "weight_input_mode",
            "active",
            "is_custom",
            "owner",
        ]
        read_only_fields = ["active", "owner"]


# --------------------------------------------------------------------
# Programs (also covers Workout / ExercisePrescription — same context)
# --------------------------------------------------------------------


class ExercisePrescriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExercisePrescription
        fields = [
            "id",
            "workout",
            "exercise",
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
        # web ModelForm path — DRF's ModelSerializer doesn't call a
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
        # template) — apps.api.views.ExercisePrescriptionViewSet's own
        # get_queryset makes the same check for update/destroy, but
        # create has no existing instance for a queryset to scope, so
        # it's enforced here instead.
        from apps.programs import services as program_services

        request = self.context["request"]
        if not program_services.editable_by(request.user).filter(pk=value.program_id).exists():
            raise serializers.ValidationError("Not a workout you can edit.")
        return value


class WorkoutSerializer(serializers.ModelSerializer):
    prescriptions = ExercisePrescriptionSerializer(many=True, read_only=True)

    class Meta:
        model = Workout
        fields = ["id", "program", "name", "order", "scheduled_weekday", "notes", "prescriptions"]

    def validate_program(self, value):
        # Same reasoning as ExercisePrescriptionSerializer.validate_workout
        # above — a workout can only be added to a program this user
        # actually owns.
        from apps.programs import services as program_services

        request = self.context["request"]
        if not program_services.editable_by(request.user).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not a program you can edit.")
        return value


class ProgramSerializer(serializers.ModelSerializer):
    workouts = WorkoutSerializer(many=True, read_only=True)
    is_system_template = serializers.BooleanField(read_only=True)

    class Meta:
        model = Program
        fields = [
            "id",
            "name",
            "description",
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
        read_only_fields = ["set_number"]  # auto-numbered — see apps.workouts.services.log_set

    def validate_performed_exercise(self, value):
        request = self.context["request"]
        if value.session.user_id != request.user.id:
            raise serializers.ValidationError("Not your workout session.")
        # Only a brand-new set requires the session still be in
        # progress — editing an existing one doesn't (apps.workouts
        # .views.set_edit has no such check either).
        if self.instance is None and not value.session.is_in_progress:
            raise serializers.ValidationError("This session is no longer in progress.")
        return value


class PerformedExerciseSerializer(serializers.ModelSerializer):
    sets = ExerciseSetSerializer(many=True, read_only=True)

    class Meta:
        model = PerformedExercise
        fields = [
            "id",
            "session",
            "exercise",
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
            # .start_session) from the plan's ExercisePrescription — an
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
        # — starting a session from a workout you can't even see would
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
        # Only checked on update — a create() carries whatever default
        # status a fresh session gets regardless of what's in the
        # payload (perform_create never reads it), so there's nothing
        # to validate there. On update, only the two "end a session"
        # transitions are ever valid (starting one goes through
        # create() instead, since it needs a workout/freeform choice,
        # not a bare status flip) — matches what the web UI's own
        # session-complete/session-abandon endpoints allow and nothing
        # more.
        if self.instance is None:
            return value
        allowed = {"completed", "abandoned"}
        if value not in allowed:
            raise serializers.ValidationError(f"status can only be set to one of {allowed}.")
        return value


# --------------------------------------------------------------------
# Measurements
# --------------------------------------------------------------------


class MeasurementTypeSerializer(serializers.ModelSerializer):
    is_custom = serializers.BooleanField(read_only=True)

    class Meta:
        model = MeasurementType
        fields = ["id", "name", "unit_kind", "active", "is_custom", "owner"]
        read_only_fields = ["active", "owner"]


class BodyMeasurementSerializer(serializers.ModelSerializer):
    class Meta:
        model = BodyMeasurement
        fields = ["id", "measurement_type", "value", "recorded_at", "notes"]

    def validate_measurement_type(self, value):
        # A user may only log against a type they can actually see
        # (system types + their own) — mirrors
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
    is_custom = serializers.BooleanField(read_only=True)

    class Meta:
        model = ActivityType
        fields = ["id", "name", "active", "is_custom", "owner"]
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
# Records (read-only — PRs are derived, never directly writable)
# --------------------------------------------------------------------


class PersonalRecordSerializer(serializers.ModelSerializer):
    record_type_display = serializers.CharField(source="get_record_type_display", read_only=True)

    class Meta:
        model = PersonalRecord
        fields = [
            "id",
            "exercise",
            "record_type",
            "record_type_display",
            "rep_count",
            "value",
            "weight",
            "reps",
            "achieved_at",
        ]


# --------------------------------------------------------------------
# Analytics (read-only aggregates — no backing model of their own)
# --------------------------------------------------------------------


class TrainingSummarySerializer(serializers.Serializer):
    session_count = serializers.IntegerField()
    total_duration_seconds = serializers.SerializerMethodField()
    total_volume = serializers.DecimalField(max_digits=12, decimal_places=2)

    def get_total_duration_seconds(self, obj):
        return obj.total_duration.total_seconds()


class AchievementSerializer(serializers.Serializer):
    icon = serializers.CharField()
    label = serializers.CharField()
    value = serializers.CharField()
    # Matches apps.analytics.achievements.Achievement's own field name
    # (renamed from `username`) — this can be "alice (Alice)", not
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
            "nutri_score",
            "nova_group",
            "off_id",
            "active",
            "owner",
        ]
        # nutri_score/nova_group/off_id are only ever set by an
        # OpenFoodFacts import (apps.nutrition.services.import_or_
        # refresh_food_from_off), never hand-entered — same reasoning
        # as docs/NUTRITION.md's own "Food" section. Importing a food
        # by barcode isn't exposed as a create here at all yet; a
        # client creates a plain hand-entered food the same way the
        # web form does (see "What's deliberately not here" below).
        read_only_fields = ["nutri_score", "nova_group", "off_id", "active", "owner"]


class MealSlotSerializer(serializers.ModelSerializer):
    is_custom = serializers.BooleanField(read_only=True)

    class Meta:
        model = MealSlot
        fields = ["id", "name", "order", "active", "is_custom", "owner"]
        read_only_fields = ["active", "owner"]


class RecipeIngredientSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeIngredient
        fields = ["id", "recipe", "food", "quantity", "order"]

    def validate_recipe(self, value):
        # Same reasoning as ExercisePrescriptionSerializer.validate_
        # workout — an ingredient can only be added to a recipe this
        # user actually owns.
        request = self.context["request"]
        if value.owner_id != request.user.id:
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
    ingredients = RecipeIngredientSerializer(many=True, read_only=True)

    class Meta:
        model = Recipe
        fields = ["id", "name", "servings", "instructions", "owner", "ingredients"]
        read_only_fields = ["owner"]


class DiaryEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = DiaryEntry
        fields = ["id", "date", "meal_slot", "food", "recipe", "quantity", "notes", "user"]
        read_only_fields = ["user"]

    def validate(self, attrs):
        # Mirrors DiaryEntry.clean() / its own CheckConstraint —
        # checked here too so a bad request gets a normal 400 with a
        # clear message instead of a raw IntegrityError 500 from the
        # database constraint alone.
        food = attrs.get("food", getattr(self.instance, "food", None))
        recipe = attrs.get("recipe", getattr(self.instance, "recipe", None))
        if bool(food) == bool(recipe):
            raise serializers.ValidationError("Log either a food or a recipe, not both or neither.")
        return attrs

    def validate_meal_slot(self, value):
        from apps.nutrition import services as nutrition_services

        request = self.context["request"]
        if not nutrition_services.visible_meal_slots(request.user).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not a meal slot you can log against.")
        return value

    def validate_food(self, value):
        from django.db.models import Q

        request = self.context["request"]
        visible = Food.objects.filter(
            Q(owner=request.user) | Q(owner__isnull=True), active=True
        )
        if not visible.filter(pk=value.pk).exists():
            raise serializers.ValidationError("Not a food you can log.")
        return value

    def validate_recipe(self, value):
        request = self.context["request"]
        if value.owner_id != request.user.id:
            raise serializers.ValidationError("Not a recipe you own.")
        return value


# Goals/targets are historized and only ever change through
# apps.nutrition.services.set_goal/set_target (append a new row,
# close the old one) — exposed read-only here for exactly the same
# reason PersonalRecordSerializer is read-only above: a raw PATCH
# through a generic serializer could silently rewrite history a
# derived/historized resource must never allow. Setting a goal or
# target through the API isn't in scope for this pass — see "What's
# deliberately not here" in docs/API.md.
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


class NutritionTargetSerializer(serializers.ModelSerializer):
    # NutritionTarget.display_reason under the API's existing "reason"
    # key — re-rendered in the request's active language when the
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


class NutritionProfileSerializer(serializers.ModelSerializer):
    # A property, not a model field (NutritionProfile.age_years,
    # derived from birth_date so it never goes stale) — exposed
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
    """`meals`/each meal's `items` are read-only here — they're always
    generated by apps.nutrition.diet_builder.build_diet_plan's own
    calorie-splitting algorithm (DietPlanViewSet.perform_create below),
    never hand-inserted through this serializer, the same "a raw write
    could silently corrupt what a domain service guarantees" reasoning
    NutritionGoal/NutritionTarget above already follow — unlike a
    recipe's own hand-edited ingredient list, a diet plan's meals/items
    are meant to stay whatever the wizard actually computed.
    `is_active` is a real Meta.read_only_fields entry — it's never
    valid input, on create or update alike. The four `target_*` fields
    aren't: DietPlanViewSet.perform_create needs them as writable input
    to pass through to `build_diet_plan`, so they can't be marked
    read-only in Meta (DRF strips a read-only field from
    validated_data unconditionally, create included). Instead
    perform_update discards them the same way it already discards
    `meal_slots` — on update only, they're a snapshot of what the plan
    was actually built against (DietPlan's own model docstring), and
    only `build_diet_plan` should ever set them; a raw PATCH changing
    them would silently disconnect a plan's stated targets from what
    its `meals` were actually generated to hit.

    `meal_slots` isn't a real DietPlan field at all — write-only input
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
