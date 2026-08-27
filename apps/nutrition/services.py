"""Orchestration for the historized goal/target chains — see
docs/NUTRITION.md "NutritionGoal"/"NutritionTarget". Views call these,
never touch the append/supersede logic directly, so there's exactly
one place a goal or target row ever gets closed out.

Also home to the nutrition-scaling functions (scale_nutrition and
friends) — these read real Food/Recipe rows and their relations, so
they belong here rather than in a pure, DB-free module like
apps.nutrition.energy/macros.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import energy, macros, openfoodfacts
from .models import (
    DietPlan,
    Food,
    NutritionGoal,
    NutritionTarget,
    OpenFoodFactsSettings,
    TargetSource,
)

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
def set_target(user, *, goal, daily_calories, macro_breakdown, source, reason, reason_data=None):
    """Same append/supersede shape as `set_goal`. `macro_breakdown` is
    an `apps.nutrition.macros.MacroBreakdown` — this is the one place
    its grams get written into a persisted `NutritionTarget` row.
    `reason_data` (an `energy.CalorieTargetReasonData`, from
    `energy.calculate_calorie_target`) is optional — passing it lets
    `NutritionTarget.display_reason` re-render `reason` in whatever
    language is active later, instead of it staying frozen in
    whichever language was active right now. Omitted by
    `apply_adjustment_suggestion` below, which has no such structured
    breakdown to snapshot; that path's `reason` stays frozen, same as
    every target did before this existed."""
    now = timezone.now()
    NutritionTarget.objects.filter(user=user, ended_at__isnull=True).update(ended_at=now)
    reason_fields = {}
    if reason_data is not None:
        reason_fields = dict(
            tdee=reason_data.tdee,
            capped_rate_kg_per_week=reason_data.capped_rate,
            rate_was_capped=reason_data.rate_was_capped,
            rate_cap_fraction_percent=reason_data.rate_cap_fraction_percent,
            raw_calories_before_floor=reason_data.raw_calories,
            calorie_floor=reason_data.floor,
            floor_was_applied=reason_data.floor_was_applied,
        )
    return NutritionTarget.objects.create(
        user=user,
        goal=goal,
        daily_calories=daily_calories,
        protein_grams=macro_breakdown.protein_grams,
        carbohydrate_grams=macro_breakdown.carbohydrate_grams,
        fat_grams=macro_breakdown.fat_grams,
        source=source,
        reason=reason,
        **reason_fields,
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
def set_active_diet_plan(user, plan):
    """Makes `plan` the one active plan for `user`, deactivating
    whichever other plan (if any) was active before — DietPlan.Meta's
    own unique constraint guarantees only one ever is; this is the one
    place that transition actually happens, the same shape as
    `set_goal`/`set_target` above for their own "only one open row"
    invariant. `diet_builder.build_diet_plan` does this same
    deactivate-then-activate step itself when generating a brand new
    plan; this is for switching to an *existing* one instead."""
    DietPlan.objects.filter(user=user, is_active=True).exclude(pk=plan.pk).update(is_active=False)
    plan.is_active = True
    plan.save(update_fields=["is_active"])


def deactivate_diet_plan(plan):
    """Turns a plan off without making any other plan active — having
    no active plan at all is a valid state (docs/NUTRITION.md "DietPlan"),
    unlike a goal or target, which always have exactly one open row."""
    plan.is_active = False
    plan.save(update_fields=["is_active"])


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


def import_or_refresh_food_from_off(barcode, *, force=False):
    """Creates a shared (`owner=None`) `Food` row from OpenFoodFacts,
    or refreshes an existing one if it's gone stale — see
    docs/NUTRITION.md "OpenFoodFacts integration": staleness-triggered
    refresh on next use, not a periodic bulk re-sync. Returns `None`
    if the integration is turned off (OpenFoodFactsSettings) or OFF
    has nothing usable for this barcode.

    `force=True` skips the staleness check and always re-fetches —
    used only by `apps.nutrition.admin.FoodAdmin`'s "Refresh selected
    foods from OpenFoodFacts" action, an explicit admin-initiated
    request for specific rows, not a scheduled or unconditional
    bulk re-sync (still the thing this whole integration was
    deliberately scoped away from — see the module docstring)."""
    if not OpenFoodFactsSettings.load().enabled:
        return None

    existing = Food.objects.filter(off_id=barcode).first()
    if existing is not None and not force and not _food_is_stale(existing):
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


@transaction.atomic
def merge_foods(keep, duplicates):
    """The admin-only "these are actually the same food" cleanup tool
    (`apps.nutrition.admin.FoodAdmin`) — re-points every reference to
    `duplicates` onto `keep`, then deletes the now-unreferenced
    duplicate rows. Deliberately never deletes a `DiaryEntry`/
    `RecipeIngredient`/`DietPlanItem` itself, only changes which
    `Food` row it points at — a duplicate entry in the food library is
    a data-quality problem, not a reason to erase what a user actually
    logged (docs/NUTRITION.md and CLAUDE.md's own "workout history
    must remain historically trustworthy" apply here too: a merge
    tidies the library, it must never look like the user's own diary
    lost an entry). `keep` is chosen by whoever calls this, not
    inferred — see the admin view for why that has to stay a human
    decision, not a heuristic."""
    from .models import DiaryEntry, DietPlanItem, RecipeIngredient

    duplicate_ids = [food.pk for food in duplicates if food.pk != keep.pk]
    if not duplicate_ids:
        return
    RecipeIngredient.objects.filter(food_id__in=duplicate_ids).update(food=keep)
    DiaryEntry.objects.filter(food_id__in=duplicate_ids).update(food=keep)
    DietPlanItem.objects.filter(food_id__in=duplicate_ids).update(food=keep)
    Food.objects.filter(pk__in=duplicate_ids).delete()


# EAN-8/UPC-A/EAN-13/ITF-14 cover every barcode format OpenFoodFacts
# itself indexes products by — a query that's nothing but 8-14 digits
# is unambiguously a barcode being typed or scanned in, not a food
# name, so it gets looked up directly rather than run through OFF's
# free-text search (which is unreliable for raw digit strings).
_BARCODE_RE = re.compile(r"^\d{8,14}$")


def search_foods(user, query):
    """Local foods (the user's own + shared/imported) matching
    `query`, plus live OpenFoodFacts results merged in. A query that
    looks like a barcode (8-14 digits — see `_BARCODE_RE`) is matched
    exactly, both locally (`Food.off_id`) and against OFF's own
    by-barcode lookup (`openfoodfacts.get_product`, the same one
    `import_or_refresh_food_from_off` uses) rather than OFF's
    free-text search, which is unreliable for a bare digit string.
    Anything else is a plain name search. OFF results aren't imported
    just for appearing in this list — only
    `import_or_refresh_food_from_off` (called once the caller actually
    picks one) ever creates or updates a `Food` row, so a search that
    finds nothing useful leaves no trace."""
    is_barcode = bool(_BARCODE_RE.match(query.strip()))
    local_filter = Q(off_id=query.strip()) if is_barcode else Q(name__icontains=query)
    local = list(
        Food.objects.filter(Q(owner=user) | Q(owner__isnull=True), active=True)
        .filter(local_filter)
        .order_by("name")
    )
    off_results = []
    if OpenFoodFactsSettings.load().enabled:
        try:
            if is_barcode:
                raw_products = [openfoodfacts.get_product(query.strip())]
            else:
                raw_products = openfoodfacts.search_products(query)
            off_results = [
                parsed
                for raw in raw_products
                if raw is not None
                and (parsed := openfoodfacts.parse_product(raw)) is not None
                # Skip anything already imported locally — no point
                # offering to "import" a food that's already there.
                and not Food.objects.filter(off_id=parsed["off_id"]).exists()
            ]
        except openfoodfacts.OpenFoodFactsError:
            off_results = []
    return local, off_results


_CATEGORY_CACHE_KEY = "nutrition:off_categories"
_CATEGORY_CACHE_SECONDS = 60 * 60 * 24  # a day — OFF's category list barely moves


def suggested_categories():
    """The "browse by category" list — cached for a day so a page
    every user visits doesn't refetch OFF's category list on every
    request; the ranking barely changes day to day, unlike a single
    product's own nutrition data. Returns `[]` (never raises) if the
    integration is off or OFF is unreachable, same "browsing degrades
    gracefully" reasoning as `openfoodfacts.list_categories` itself."""
    if not OpenFoodFactsSettings.load().enabled:
        return []
    cached = cache.get(_CATEGORY_CACHE_KEY)
    if cached is not None:
        return cached
    categories = openfoodfacts.list_categories()
    cache.set(_CATEGORY_CACHE_KEY, categories, _CATEGORY_CACHE_SECONDS)
    return categories


def browse_category(category_id):
    """Live OFF results for one category, same "skip anything already
    imported locally" filtering as `search_foods` — browsing and
    searching both only ever show an OFF product as an *importable*
    result once, never a duplicate of something already in the user's
    own library."""
    if not OpenFoodFactsSettings.load().enabled:
        return []
    try:
        raw_products = openfoodfacts.search_by_category(category_id)
    except openfoodfacts.OpenFoodFactsError:
        return []
    return [
        parsed
        for raw in raw_products
        if (parsed := openfoodfacts.parse_product(raw)) is not None
        and not Food.objects.filter(off_id=parsed["off_id"]).exists()
    ]


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


@dataclass(frozen=True)
class FoodUsage:
    """One entry in a "quick add" panel — see `most_used_foods`.
    `quantity`/`meal_slot_id` are sensible defaults to prefill, not
    part of what ranks the food: the most recent diary quantity/meal
    for that food if it's ever been logged directly, else the food's
    own serving size and no meal-slot guess."""

    food: object
    quantity: object
    meal_slot_id: object


def most_used_foods(user, *, limit=10):
    """The user's most frequently added foods, most-used first —
    powers the "quick add" panel shown wherever a food can be added
    (the food diary, recipe ingredients, diet-plan meal items), so a
    food eaten often doesn't need re-typing into a search box every
    single time. Ranked by frequency, not recency: usage is counted
    across every place a food actually gets added for this user — the
    diary, recipe ingredients, and diet-plan items — not just one of
    them, since "used the most" means overall, not "most recent in
    this one context." Only ever counts this user's own usage, even
    for a shared (`owner=None`) food other users also have."""
    from collections import Counter

    from .models import DiaryEntry, DietPlanItem, Food, RecipeIngredient

    counts = Counter()
    counts.update(
        DiaryEntry.objects.filter(user=user, food__isnull=False).values_list(
            "food_id", flat=True
        )
    )
    counts.update(RecipeIngredient.objects.filter(recipe__owner=user).values_list(
        "food_id", flat=True
    ))
    counts.update(
        DietPlanItem.objects.filter(
            diet_plan_meal__diet_plan__user=user, food__isnull=False
        ).values_list("food_id", flat=True)
    )
    if not counts:
        return []

    top_ids = [food_id for food_id, _count in counts.most_common(limit)]
    foods_by_id = Food.objects.in_bulk(top_ids)

    # The most recent diary entry per food, for a sensible quantity/
    # meal-slot prefill — Postgres DISTINCT ON, matches this project's
    # only supported database (docs/ARCHITECTURE.md).
    last_diary_use = {
        food_id: (quantity, meal_slot_id)
        for food_id, quantity, meal_slot_id in DiaryEntry.objects.filter(
            user=user, food_id__in=top_ids
        )
        .order_by("food_id", "-created_at")
        .distinct("food_id")
        .values_list("food_id", "quantity", "meal_slot_id")
    }

    usages = []
    for food_id in top_ids:
        food = foods_by_id.get(food_id)
        if food is None:
            continue
        quantity, meal_slot_id = last_diary_use.get(food_id, (food.serving_size, None))
        usages.append(FoodUsage(food=food, quantity=quantity, meal_slot_id=meal_slot_id))
    return usages


@transaction.atomic
def copy_diary_day(user, source_date, target_date):
    """Duplicates every DiaryEntry logged on `source_date` onto
    `target_date` as brand new rows — "I ate the same as yesterday"
    without re-searching and re-adding every item by hand. The source
    day is never read back out or mutated (docs/NUTRITION.md /
    CLAUDE.md's "history must remain historically trustworthy"
    extends here the same way it does to `merge_foods`), and copying
    is purely additive: running it twice, or copying onto a day that
    already has entries, just adds more rows rather than silently
    deduplicating — the same "an explicit user action always does
    exactly what it says" rule as every other diary action in this
    app. Returns how many entries were copied, so the caller can tell
    a genuinely empty source day apart from a successful copy."""
    from .models import DiaryEntry

    source_entries = DiaryEntry.objects.filter(user=user, date=source_date)
    new_entries = [
        DiaryEntry(
            user=user,
            date=target_date,
            meal_slot_id=entry.meal_slot_id,
            food_id=entry.food_id,
            recipe_id=entry.recipe_id,
            quantity=entry.quantity,
            notes=entry.notes,
        )
        for entry in source_entries
    ]
    DiaryEntry.objects.bulk_create(new_entries)
    return len(new_entries)


# How many days of calorie_history/nutrition_stats look back by
# default — long enough to see a real weekly pattern (weekday vs.
# weekend eating), short enough to stay one screen's worth of bars,
# same "30" apps.nutrition.dashboard's own weight chart already uses
# for the same reason (docs/NUTRITION.md dashboard weight chart).
STATS_WINDOW_DAYS = 30


def calorie_history(user, *, days=STATS_WINDOW_DAYS):
    """The last `days` calendar days' totals (today inclusive), oldest
    first, with exactly one entry per day — including days nothing was
    logged at all (`ZERO_NUTRITION`), the same "always one point per
    day, not just days with data" shape
    apps.analytics.services.weekly_volume_series uses, so a genuinely
    quiet day shows up as a real dip on the chart rather than a gap
    that silently compresses the timeline."""
    from .models import DiaryEntry

    today = timezone.localdate()
    start = today - timezone.timedelta(days=days - 1)
    entries = DiaryEntry.objects.filter(
        user=user, date__gte=start, date__lte=today
    ).select_related("food", "recipe")

    totals_by_date = {}
    for entry in entries:
        totals_by_date[entry.date] = totals_by_date.get(
            entry.date, ZERO_NUTRITION
        ) + diary_entry_nutrition(entry)

    return [
        (start + timezone.timedelta(days=offset), totals_by_date.get(
            start + timezone.timedelta(days=offset), ZERO_NUTRITION
        ))
        for offset in range(days)
    ]


@dataclass(frozen=True)
class NutritionStatsSummary:
    """The nutrition stats page's headline numbers — see
    `nutrition_stats`."""

    days_logged: int
    days_in_range: int
    average_calories: Decimal
    average_protein_grams: Decimal
    average_carbohydrate_grams: Decimal
    average_fat_grams: Decimal


def nutrition_stats(user, *, days=STATS_WINDOW_DAYS) -> NutritionStatsSummary:
    """Average daily calories/macros over `calorie_history`'s window,
    counting only days something was actually logged. An unlogged day
    is excluded from the average rather than counted as a zero-calorie
    day — counting it as zero would drag the average down for anyone
    who only logs most days, not every single day, which is most
    real usage and shouldn't be punished by the stats page itself."""
    history = calorie_history(user, days=days)
    logged = [totals for _day, totals in history if totals.calories > 0]
    days_logged = len(logged)
    if not days_logged:
        return NutritionStatsSummary(
            days_logged=0,
            days_in_range=days,
            average_calories=Decimal("0"),
            average_protein_grams=Decimal("0"),
            average_carbohydrate_grams=Decimal("0"),
            average_fat_grams=Decimal("0"),
        )

    count = Decimal(days_logged)

    def _average(attr, places):
        total = sum((getattr(totals, attr) for totals in logged), Decimal("0"))
        return (total / count).quantize(Decimal(places))

    return NutritionStatsSummary(
        days_logged=days_logged,
        days_in_range=days,
        average_calories=_average("calories", "1"),
        average_protein_grams=_average("protein_grams", "0.1"),
        average_carbohydrate_grams=_average("carbohydrate_grams", "0.1"),
        average_fat_grams=_average("fat_grams", "0.1"),
    )


def is_training_day(user, target_date):
    """Whether `target_date` has at least one *completed* workout
    session — the same filter apps.analytics.services uses to define
    "training day" everywhere else. Informational only
    (docs/NUTRITION.md "Integration with existing apps" — no separate
    training-day calorie target is derived from this in this pass, it
    only labels the dashboard)."""
    from apps.workouts.models import WorkoutSession, WorkoutSessionStatus

    return WorkoutSession.objects.filter(
        user=user, status=WorkoutSessionStatus.COMPLETED, started_at__date=target_date
    ).exists()
