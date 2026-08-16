"""Orchestration for the historized goal/target chains — see
docs/NUTRITION.md "NutritionGoal"/"NutritionTarget". Views call these,
never touch the append/supersede logic directly, so there's exactly
one place a goal or target row ever gets closed out.

Also home to the nutrition-scaling functions (scale_nutrition and
friends) — these read real Food/Recipe rows and their relations, so
they belong here rather than in a pure, DB-free module like
apps.nutrition.energy/macros.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import energy, macros, openfoodfacts
from .models import Food, NutritionGoal, NutritionTarget, OpenFoodFactsSettings, TargetSource

# See docs/NUTRITION.md "OpenFoodFacts integration" — the interval a
# food imported from OFF is trusted before being transparently
# re-fetched on next use, instead of a periodic bulk re-sync.
OPENFOODFACTS_STALENESS_DAYS = 14


@transaction.atomic
def set_goal(user, *, goal_type, target_rate_kg_per_week, target_weight=None, notes=""):
    """Ends whichever goal is currently open (if any) and starts a new
    one — never mutates an existing row in place (docs/NUTRITION.md
    "NutritionGoal")."""
    now = timezone.now()
    NutritionGoal.objects.filter(user=user, ended_at__isnull=True).update(ended_at=now)
    return NutritionGoal.objects.create(
        user=user,
        goal_type=goal_type,
        target_rate_kg_per_week=target_rate_kg_per_week,
        target_weight=target_weight,
        notes=notes,
    )


@transaction.atomic
def set_target(user, *, goal, daily_calories, macro_breakdown, source, reason):
    """Same append/supersede shape as `set_goal`. `macro_breakdown` is
    an `apps.nutrition.macros.MacroBreakdown` — this is the one place
    its grams get written into a persisted `NutritionTarget` row."""
    now = timezone.now()
    NutritionTarget.objects.filter(user=user, ended_at__isnull=True).update(ended_at=now)
    return NutritionTarget.objects.create(
        user=user,
        goal=goal,
        daily_calories=daily_calories,
        protein_grams=macro_breakdown.protein_grams,
        carbohydrate_grams=macro_breakdown.carbohydrate_grams,
        fat_grams=macro_breakdown.fat_grams,
        source=source,
        reason=reason,
    )


def calculate_target_for_goal(profile, *, weight_kg, height_cm, goal_type, target_rate_kg_per_week):
    """The full profile+goal -> (calorie target, macro breakdown)
    pipeline — used by both the onboarding wizard (a brand new goal)
    and changing goals later. Pure composition of
    apps.nutrition.energy/macros; no DB writes. `weight_kg`/
    `height_cm` are explicit rather than read off `profile`, since
    neither lives there — weight comes from apps.measurements, height
    from apps.accounts.User (docs/NUTRITION.md "Why a new app")."""
    age_years = profile.age_years
    bmr = energy.calculate_bmr(weight_kg, height_cm, age_years, profile.biological_sex)
    tdee = energy.calculate_tdee(bmr, profile.activity_level)
    calorie_result = energy.calculate_calorie_target(
        tdee=tdee,
        weight_kg=weight_kg,
        height_cm=height_cm,
        age_years=age_years,
        biological_sex=profile.biological_sex,
        goal_type=goal_type,
        target_rate_kg_per_week=target_rate_kg_per_week,
    )
    macro_result = macros.calculate_macros(weight_kg, calorie_result.daily_calories, goal_type)
    return calorie_result, macro_result


@transaction.atomic
def apply_adjustment_suggestion(user, suggestion):
    """Accepting a dynamic-adjustment suggestion
    (apps.nutrition.suggestions) — never automatic, always this one
    explicit call from a user action. Keeps the same goal and macro
    split, just moves daily_calories by the suggested delta and
    rebalances macros against the new total."""
    from apps.measurements.models import MeasurementType
    from apps.measurements.services import latest_for

    current_target = NutritionTarget.objects.get(user=user, ended_at__isnull=True)
    goal = current_target.goal

    body_weight_type = MeasurementType.objects.filter(name="Body weight", owner=None).first()
    latest_weight = latest_for(user, body_weight_type) if body_weight_type else None
    # A suggestion is only ever produced once trend data exists, so
    # this should always resolve — guarded anyway rather than assume.
    weight_kg = latest_weight.value if latest_weight else None

    macro_breakdown = macros.calculate_macros(
        weight_kg, suggestion.suggested_daily_calories, goal.goal_type
    )
    return set_target(
        user,
        goal=goal,
        daily_calories=suggestion.suggested_daily_calories,
        macro_breakdown=macro_breakdown,
        source=TargetSource.ADJUSTED,
        reason=suggestion.reason,
    )


def _food_is_stale(food):
    if food.off_synced_at is None:
        return True
    return (timezone.now() - food.off_synced_at).days >= OPENFOODFACTS_STALENESS_DAYS


def import_or_refresh_food_from_off(barcode):
    """Creates a shared (`owner=None`) `Food` row from OpenFoodFacts,
    or refreshes an existing one if it's gone stale — see
    docs/NUTRITION.md "OpenFoodFacts integration": staleness-triggered
    refresh on next use, not a periodic bulk re-sync. Returns `None`
    if the integration is turned off (OpenFoodFactsSettings) or OFF
    has nothing usable for this barcode."""
    if not OpenFoodFactsSettings.load().enabled:
        return None

    existing = Food.objects.filter(off_id=barcode).first()
    if existing is not None and not _food_is_stale(existing):
        return existing

    try:
        raw = openfoodfacts.get_product(barcode)
    except openfoodfacts.OpenFoodFactsError:
        # A flaky third-party API must never break a lookup that
        # already has a (merely stale) local row to fall back on.
        return existing
    if raw is None:
        return existing

    parsed = openfoodfacts.parse_product(raw)
    if parsed is None:
        return existing

    parsed["off_synced_at"] = timezone.now()
    if existing is not None:
        for field, value in parsed.items():
            setattr(existing, field, value)
        existing.save()
        return existing
    return Food.objects.create(owner=None, **parsed)


def search_foods(user, query):
    """Local foods (the user's own + shared/imported) matching
    `query`, plus live OpenFoodFacts search results merged in. OFF
    results aren't imported just for appearing in this list — only
    `import_or_refresh_food_from_off` (called once the caller actually
    picks one) ever creates or updates a `Food` row, so a search that
    finds nothing useful leaves no trace."""
    local = list(
        Food.objects.filter(Q(owner=user) | Q(owner__isnull=True), active=True)
        .filter(name__icontains=query)
        .order_by("name")
    )
    off_results = []
    if OpenFoodFactsSettings.load().enabled:
        try:
            off_results = [
                parsed
                for raw in openfoodfacts.search_products(query)
                if (parsed := openfoodfacts.parse_product(raw)) is not None
                # Skip anything already imported locally — no point
                # offering to "import" a food that's already there.
                and not Food.objects.filter(off_id=parsed["off_id"]).exists()
            ]
        except openfoodfacts.OpenFoodFactsError:
            off_results = []
    return local, off_results


@dataclass(frozen=True)
class ScaledNutrition:
    """One food/recipe amount's nutrition, already scaled to whatever
    quantity was actually logged — the shape apps.nutrition.services.
    scale_nutrition/diary_entry_nutrition/recipe_total_nutrition all
    return, and what the food diary/recipe templates render directly.
    """

    calories: Decimal
    protein_grams: Decimal
    carbohydrate_grams: Decimal
    fat_grams: Decimal
    fiber_grams: Decimal | None
    sugar_grams: Decimal | None
    saturated_fat_grams: Decimal | None
    sodium_mg: Decimal | None

    def __add__(self, other):
        def _add(a, b):
            if a is None and b is None:
                return None
            return (a or Decimal("0")) + (b or Decimal("0"))

        return ScaledNutrition(
            calories=self.calories + other.calories,
            protein_grams=self.protein_grams + other.protein_grams,
            carbohydrate_grams=self.carbohydrate_grams + other.carbohydrate_grams,
            fat_grams=self.fat_grams + other.fat_grams,
            fiber_grams=_add(self.fiber_grams, other.fiber_grams),
            sugar_grams=_add(self.sugar_grams, other.sugar_grams),
            saturated_fat_grams=_add(self.saturated_fat_grams, other.saturated_fat_grams),
            sodium_mg=_add(self.sodium_mg, other.sodium_mg),
        )

    def scaled_by(self, factor):
        def _scale(value):
            return value * factor if value is not None else None

        return ScaledNutrition(
            calories=self.calories * factor,
            protein_grams=self.protein_grams * factor,
            carbohydrate_grams=self.carbohydrate_grams * factor,
            fat_grams=self.fat_grams * factor,
            fiber_grams=_scale(self.fiber_grams),
            sugar_grams=_scale(self.sugar_grams),
            saturated_fat_grams=_scale(self.saturated_fat_grams),
            sodium_mg=_scale(self.sodium_mg),
        )


ZERO_NUTRITION = ScaledNutrition(
    calories=Decimal("0"),
    protein_grams=Decimal("0"),
    carbohydrate_grams=Decimal("0"),
    fat_grams=Decimal("0"),
    fiber_grams=None,
    sugar_grams=None,
    saturated_fat_grams=None,
    sodium_mg=None,
)


def scale_nutrition(food, quantity) -> ScaledNutrition:
    """`food`'s own nutrition values, scaled from its `serving_size`
    to `quantity` (same unit as `food.serving_unit`) — the one place
    this ratio is computed, reused by recipe totals and a diary entry
    logging raw food directly (docs/NUTRITION.md "RecipeIngredient")."""
    ratio = quantity / food.serving_size
    return ScaledNutrition(
        calories=Decimal(food.calories) * ratio,
        protein_grams=food.protein_grams * ratio,
        carbohydrate_grams=food.carbohydrate_grams * ratio,
        fat_grams=food.fat_grams * ratio,
        fiber_grams=food.fiber_grams * ratio if food.fiber_grams is not None else None,
        sugar_grams=food.sugar_grams * ratio if food.sugar_grams is not None else None,
        saturated_fat_grams=(
            food.saturated_fat_grams * ratio if food.saturated_fat_grams is not None else None
        ),
        sodium_mg=food.sodium_mg * ratio if food.sodium_mg is not None else None,
    )


def recipe_total_nutrition(recipe) -> ScaledNutrition:
    """Sum of every ingredient's own scaled nutrition."""
    total = ZERO_NUTRITION
    for ingredient in recipe.ingredients.select_related("food"):
        total = total + scale_nutrition(ingredient.food, ingredient.quantity)
    return total


def recipe_per_serving_nutrition(recipe) -> ScaledNutrition:
    servings = Decimal(recipe.servings) if recipe.servings else Decimal("1")
    return recipe_total_nutrition(recipe).scaled_by(Decimal("1") / servings)


def diary_entry_nutrition(entry) -> ScaledNutrition:
    """A single DiaryEntry's nutrition — dispatches on whichever of
    `food`/`recipe` is set (the model's own CheckConstraint guarantees
    exactly one is). `quantity` means the food's own unit for a food
    entry, servings for a recipe entry."""
    if entry.food_id:
        return scale_nutrition(entry.food, entry.quantity)
    return recipe_per_serving_nutrition(entry.recipe).scaled_by(entry.quantity)


def daily_totals(user, target_date) -> ScaledNutrition:
    """Every DiaryEntry logged for `user` on `target_date`, summed —
    the figure the food diary's "today so far" header shows."""
    from .models import DiaryEntry

    total = ZERO_NUTRITION
    entries = DiaryEntry.objects.filter(user=user, date=target_date).select_related(
        "food", "recipe"
    )
    for entry in entries:
        total = total + diary_entry_nutrition(entry)
    return total


def visible_meal_slots(user):
    """System defaults + this user's own, active only — same
    system-or-custom visibility rule as apps.measurements.services.
    visible_to."""
    from .models import MealSlot

    return MealSlot.objects.filter(Q(owner=user) | Q(owner__isnull=True), active=True)
