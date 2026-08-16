"""Domain model for the nutrition/calorie system — see docs/NUTRITION.md
for the full reasoning behind every model and field here, not just what
each one is.

Two historized "append, don't mutate" chains, same shape as
apps.records.PersonalRecord: NutritionGoal (the user's stated intent)
and NutritionTarget (the derived, numeric calories/macros output).
Setting a new one stamps `ended_at` on whichever row was previously
open rather than overwriting it in place — see
apps.nutrition.services.set_goal/set_target.

Calories are always kcal — unlike weight/length (apps.core.units,
apps.measurements.units), there's no per-user display unit to convert
between, so no unit_kind-style dispatch layer exists here.
"""

from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class BiologicalSex(models.TextChoices):
    """A physiological input to the Mifflin-St Jeor BMR formula
    (apps.nutrition.energy.calculate_bmr), not a gender-identity
    field — see docs/NUTRITION.md "NutritionProfile"."""

    MALE = "male", _("Male")
    FEMALE = "female", _("Female")


class ActivityJob(models.TextChoices):
    SEDENTARY = "sedentary", _("Desk job, mostly sitting")
    LIGHT = "light", _("On my feet some of the day")
    MODERATE = "moderate", _("On my feet most of the day")
    PHYSICAL = "physical", _("Physically demanding / manual labor")


class ActivityLevel(models.TextChoices):
    """The actual TDEE-multiplier bucket (apps.nutrition.energy.
    ACTIVITY_MULTIPLIERS) — suggested from the more concrete fields
    above via apps.nutrition.energy.suggest_activity_level rather than
    asked directly, then confirmed/overridden by the user. See
    docs/NUTRITION.md "Choosing an activity level"."""

    SEDENTARY = "sedentary", _("Sedentary")
    LIGHT = "light", _("Lightly active")
    MODERATE = "moderate", _("Moderately active")
    ACTIVE = "active", _("Active")
    VERY_ACTIVE = "very_active", _("Very active")


class GoalType(models.TextChoices):
    FAT_LOSS_AGGRESSIVE = "fat_loss_aggressive", _("Fat loss — aggressive")
    FAT_LOSS_MODERATE = "fat_loss_moderate", _("Fat loss — moderate")
    FAT_LOSS_CONSERVATIVE = "fat_loss_conservative", _("Fat loss — conservative")
    MAINTENANCE = "maintenance", _("Maintenance")
    MUSCLE_GAIN_LEAN = "muscle_gain_lean", _("Muscle gain — lean bulk")
    MUSCLE_GAIN_MODERATE = "muscle_gain_moderate", _("Muscle gain — moderate bulk")
    MUSCLE_GAIN_AGGRESSIVE = "muscle_gain_aggressive", _("Muscle gain — aggressive bulk")


class TargetSource(models.TextChoices):
    CALCULATED = "calculated", _("Calculated from goal")
    MANUAL = "manual", _("Set manually")
    ADJUSTED = "adjusted", _("Accepted a suggested adjustment")


class ServingUnit(models.TextChoices):
    GRAM = "g", _("g")
    MILLILITER = "ml", _("ml")
    PIECE = "piece", _("piece")


class NutritionProfile(TimeStampedModel):
    """Current physiological/lifestyle facts the energy engine
    (apps.nutrition.energy) needs — one row per user, not historized
    (see docs/NUTRITION.md: these are inputs a user corrects in place,
    like User.height, not decisions worth preserving like a goal is).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name="nutrition_profile", on_delete=models.CASCADE
    )
    biological_sex = models.CharField(max_length=10, choices=BiologicalSex.choices)
    birth_date = models.DateField(
        help_text=_("Stored instead of a raw age, so it never goes stale.")
    )
    activity_job = models.CharField(max_length=10, choices=ActivityJob.choices)
    daily_steps = models.PositiveIntegerField(null=True, blank=True)
    training_sessions_per_week = models.PositiveSmallIntegerField(null=True, blank=True)
    training_session_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    other_exercise_minutes_per_week = models.PositiveSmallIntegerField(null=True, blank=True)
    activity_level = models.CharField(max_length=15, choices=ActivityLevel.choices)
    self_reported_daily_calories = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_(
            "If you already track this, tell us — used only to compare against this "
            "app's own estimate, never as an input to it."
        ),
    )

    def __str__(self):
        return f"Nutrition profile: {self.user.username}"

    @property
    def age_years(self):
        today = date.today()
        had_birthday_this_year = (today.month, today.day) >= (
            self.birth_date.month,
            self.birth_date.day,
        )
        return today.year - self.birth_date.year - (0 if had_birthday_this_year else 1)


class NutritionGoal(TimeStampedModel):
    """The user's stated intent — historized (append, don't mutate),
    same shape as apps.records.PersonalRecord's immutable log, just
    for a decision rather than an achievement. See
    apps.nutrition.services.set_goal for how a new row supersedes the
    previous open one."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="nutrition_goals", on_delete=models.CASCADE
    )
    goal_type = models.CharField(max_length=25, choices=GoalType.choices)
    # Canonical kilograms, same as apps.measurements.BodyMeasurement —
    # optional, since a goal like "maintenance" has no target weight.
    target_weight = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    # Signed: negative for fat loss, positive for muscle gain, 0 for
    # maintenance. See docs/NUTRITION.md "Calorie target from a goal"
    # for why this — not a raw calorie delta — is what the user sets.
    target_rate_kg_per_week = models.DecimalField(max_digits=5, decimal_places=3)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            # Only one open (not-yet-superseded) goal per user at a
            # time — the "current goal" query is always
            # filter(user=user, ended_at__isnull=True), same shape as
            # WorkoutSession's "in-progress session" and
            # NutritionTarget's "current target" below.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(ended_at__isnull=True),
                name="unique_open_nutrition_goal_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.get_goal_type_display()}"


class NutritionTarget(TimeStampedModel):
    """The derived, numeric output — historized the same way as
    NutritionGoal above. Deliberately one model for calories *and*
    macros, not two: they're always set together (macros are computed
    *from* the calorie figure) and the spec asks for them to be
    historized as one unit — see docs/NUTRITION.md "NutritionTarget"
    for why this deviates from the example CalorieTarget/MacroTarget
    split."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="nutrition_targets", on_delete=models.CASCADE
    )
    # CASCADE, not PROTECT: a goal and its targets always belong to
    # the same user and are only ever deleted together (deleting the
    # user cascades to both) — PROTECT would block exactly that
    # cascade, since Django's deletion collector checks a PROTECT
    # relation before resolving whether the referencer is also being
    # deleted in the same operation. There is no scenario where a
    # goal should survive its own targets being deleted, or vice
    # versa.
    goal = models.ForeignKey(
        NutritionGoal, related_name="targets", on_delete=models.CASCADE, null=True, blank=True
    )
    daily_calories = models.PositiveIntegerField()
    protein_grams = models.DecimalField(max_digits=6, decimal_places=2)
    carbohydrate_grams = models.DecimalField(max_digits=6, decimal_places=2)
    fat_grams = models.DecimalField(max_digits=6, decimal_places=2)
    source = models.CharField(max_length=10, choices=TargetSource.choices)
    # Human-readable explanation, same convention as
    # apps.progression.engine.ProgressionResult.reason — every target
    # can say why it is what it is, never a bare number.
    reason = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(ended_at__isnull=True),
                name="unique_open_nutrition_target_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.daily_calories} kcal"


class MealSlot(models.Model):
    """Named diary categories — Breakfast/Lunch/Dinner/Evening snack
    seeded as system defaults (owner=None), a user can add their own.
    Exactly apps.measurements.MeasurementType's owner-nullable
    system-or-custom pattern reused verbatim — see
    docs/NUTRITION.md "MealSlot"."""

    name = models.CharField(max_length=50)
    order = models.PositiveSmallIntegerField(default=0)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="custom_meal_slots",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text=_("Null for the built-in default meal slots."),
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(owner__isnull=True),
                name="unique_system_meal_slot_name",
            ),
            models.UniqueConstraint(
                fields=["owner", "name"],
                condition=models.Q(owner__isnull=False),
                name="unique_user_meal_slot_name",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_custom(self):
        return self.owner_id is not None


class Food(TimeStampedModel):
    """A trackable food, with nutrition values per `serving_size` of
    `serving_unit`. `owner` nullable, matching MealSlot/
    MeasurementType's system-or-custom split: a user-created food has
    `owner=request.user`; a food imported from OpenFoodFacts
    (apps.nutrition.openfoodfacts, docs/NUTRITION.md "OpenFoodFacts
    integration") gets `owner=None` — a shared row every user can see
    and log, the same as a system MeasurementType."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="foods",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=200)
    brand = models.CharField(max_length=200, blank=True)
    serving_size = models.DecimalField(max_digits=8, decimal_places=2)
    serving_unit = models.CharField(max_length=10, choices=ServingUnit.choices)
    calories = models.PositiveIntegerField()
    protein_grams = models.DecimalField(max_digits=6, decimal_places=2)
    carbohydrate_grams = models.DecimalField(max_digits=6, decimal_places=2)
    fat_grams = models.DecimalField(max_digits=6, decimal_places=2)
    # Optional extras — nullable, not defaulted to 0, so "unknown" is
    # never confused with "genuinely zero" (docs/NUTRITION.md "Food").
    fiber_grams = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    sugar_grams = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    saturated_fat_grams = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    sodium_mg = models.PositiveIntegerField(null=True, blank=True)
    # Set only for a food imported from OpenFoodFacts — their own
    # barcode, unique when present (Postgres allows any number of
    # NULLs alongside a unique constraint, so this stays optional for
    # every user-created food). off_synced_at drives the staleness
    # check apps.nutrition.services.import_or_refresh_food_from_off
    # uses to decide whether to re-fetch before this food is next
    # used, rather than a periodic bulk re-sync.
    off_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    off_synced_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.brand})" if self.brand else self.name


class Recipe(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="recipes", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=200)
    servings = models.PositiveSmallIntegerField(default=1)
    instructions = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RecipeIngredient(models.Model):
    """`quantity` is in the same unit as `food.serving_unit` — nutrition
    for this line is `food`'s per-serving values scaled by
    `quantity / food.serving_size` (apps.nutrition.services.
    scale_nutrition), the same scaling a DiaryEntry logging raw food
    uses — one function, not two copies of the same math."""

    recipe = models.ForeignKey(Recipe, related_name="ingredients", on_delete=models.CASCADE)
    # CASCADE, not PROTECT: same reasoning as NutritionTarget.goal
    # above — a food and the recipe ingredients that reference it are
    # very often owned by the same user, so deleting that user cascades
    # to both at once, and PROTECT would block exactly that cascade.
    # Retiring a Food a normal user shouldn't touch is `active=False`
    # (soft-delete), not a hard delete this FK needs to guard against.
    food = models.ForeignKey(Food, related_name="+", on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=8, decimal_places=2)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.quantity} {self.food.serving_unit} {self.food.name}"


class DiaryEntry(TimeStampedModel):
    """One logged item. `date` is the day it counts toward (can be
    legitimately back-dated); `created_at` (from TimeStampedModel) is
    when it was actually logged — the same split as
    apps.workouts.ExerciseSet's performed_at vs. created_at."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="diary_entries", on_delete=models.CASCADE
    )
    date = models.DateField(db_index=True)
    # CASCADE throughout — see RecipeIngredient.food's own comment.
    meal_slot = models.ForeignKey(MealSlot, related_name="+", on_delete=models.CASCADE)
    food = models.ForeignKey(
        Food, related_name="diary_entries", null=True, blank=True, on_delete=models.CASCADE
    )
    recipe = models.ForeignKey(
        Recipe, related_name="diary_entries", null=True, blank=True, on_delete=models.CASCADE
    )
    # Grams/ml/pieces for a food entry, servings for a recipe entry.
    quantity = models.DecimalField(max_digits=8, decimal_places=2)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            # Exactly one of food/recipe — enforced here, not just by
            # convention (docs/NUTRITION.md "DiaryEntry").
            models.CheckConstraint(
                condition=(
                    models.Q(food__isnull=False, recipe__isnull=True)
                    | models.Q(food__isnull=True, recipe__isnull=False)
                ),
                name="diary_entry_exactly_one_of_food_or_recipe",
            ),
        ]

    def clean(self):
        if bool(self.food_id) == bool(self.recipe_id):
            raise ValidationError(_("Log either a food or a recipe, not both or neither."))

    def __str__(self):
        item = self.food.name if self.food_id else self.recipe.name
        return f"{self.user.username}: {item} ({self.date})"


class DietPlan(TimeStampedModel):
    """The diet-builder wizard's saved output — snapshots the targets
    it was built against (immutable, so a past plan stays
    interpretable even after the user's live targets change). Applying
    it to a date materializes DiaryEntry rows
    (apps.nutrition.services.apply_diet_plan) without mutating the
    plan itself, so it can be reused across many days."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="diet_plans", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=200)
    goal = models.ForeignKey(
        NutritionGoal, related_name="diet_plans", null=True, blank=True, on_delete=models.SET_NULL
    )
    target_calories = models.PositiveIntegerField()
    target_protein_grams = models.DecimalField(max_digits=6, decimal_places=2)
    target_carbohydrate_grams = models.DecimalField(max_digits=6, decimal_places=2)
    target_fat_grams = models.DecimalField(max_digits=6, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username}: {self.name}"


class DietPlanMeal(models.Model):
    diet_plan = models.ForeignKey(DietPlan, related_name="meals", on_delete=models.CASCADE)
    # CASCADE — see RecipeIngredient.food's own comment.
    meal_slot = models.ForeignKey(MealSlot, related_name="+", on_delete=models.CASCADE)
    target_calories = models.PositiveIntegerField()
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.diet_plan.name}: {self.meal_slot.name}"


class DietPlanItem(models.Model):
    diet_plan_meal = models.ForeignKey(
        DietPlanMeal, related_name="items", on_delete=models.CASCADE
    )
    # CASCADE — see RecipeIngredient.food's own comment.
    food = models.ForeignKey(
        Food, related_name="+", null=True, blank=True, on_delete=models.CASCADE
    )
    recipe = models.ForeignKey(
        Recipe, related_name="+", null=True, blank=True, on_delete=models.CASCADE
    )
    quantity = models.DecimalField(max_digits=8, decimal_places=2)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(food__isnull=False, recipe__isnull=True)
                    | models.Q(food__isnull=True, recipe__isnull=False)
                ),
                name="diet_plan_item_exactly_one_of_food_or_recipe",
            ),
        ]

    def clean(self):
        if bool(self.food_id) == bool(self.recipe_id):
            raise ValidationError(_("Use either a food or a recipe, not both or neither."))

    def __str__(self):
        item = self.food.name if self.food_id else self.recipe.name
        return f"{self.quantity} {item}"


class OpenFoodFactsSettings(models.Model):
    """Singleton — same pattern as apps.core.models.BackupSettings/
    FeedbackSettings and apps.accounts.models.SiteDisclaimer. The only
    way to turn off outbound OpenFoodFacts requests entirely (no
    internet egress, or an operator's own preference not to call a
    third-party service from their server) — see docs/NUTRITION.md
    "OpenFoodFacts integration"."""

    enabled = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — deleting it would just silently recreate the default on next load()

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "OpenFoodFacts settings"
