"""Orchestration for the historized goal/target chains — see
docs/NUTRITION.md "NutritionGoal"/"NutritionTarget". Views call these,
never touch the append/supersede logic directly, so there's exactly
one place a goal or target row ever gets closed out.
"""

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
