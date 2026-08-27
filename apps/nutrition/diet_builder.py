"""Diet-plan generation — see docs/NUTRITION.md "Diet builder wizard".

Deliberately one simple, explainable heuristic (nearest-calorie-match
from the user's own food/recipe library, scaled to fit the meal's
calorie budget as closely as that item's own units allow) — not a
multi-item knapsack solver, and not a constraint system respecting
food preferences, avoided foods, or budget. Those are natural
extension points (this module's own function signatures leave room to
add them as further filters/scoring later), not built now, matching
CLAUDE.md's "avoid premature optimization": a plan the user can freely
swap any single item out of (docs/NUTRITION.md "DietPlan" — the whole
reason `DietPlanItem` is its own row, not baked into the plan) is more
useful sooner than a more elaborate generator that takes far longer to
get right.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from . import services
from .models import DietPlan, DietPlanItem, DietPlanMeal, Food, Recipe


@dataclass(frozen=True)
class SuggestedItem:
    food: Food | None
    recipe: Recipe | None
    quantity: Decimal


def suggest_item_for_calorie_budget(
    user, target_calories: Decimal, *, exclude: set | None = None, meal_slot=None
) -> SuggestedItem | None:
    """The single food/recipe from `user`'s own library (+ shared
    foods/recipes — the built-in template recipes, apps.nutrition.
    migrations' seed data, included the same way a shared Food already
    was) whose natural, unscaled calorie figure is closest to
    `target_calories`, then scaled to hit that budget exactly. `None`
    if the user has no foods or recipes to suggest from yet.

    `meal_slot`, if given, excludes any recipe tagged for a
    *different* one (Recipe.meal_slot — found live: a "Chicken & rice
    bowl" recipe suggested for breakfast, "Oats & yogurt" for dinner,
    because calorie-closeness alone has no idea either recipe was
    written for a specific meal). A recipe with no meal_slot of its
    own (every recipe before that field existed, and any a user
    doesn't bother tagging) stays eligible for every meal, same as
    today. Plain Food is never filtered this way — an ingredient like
    "chicken breast" isn't "a breakfast food" or "a dinner food" the
    way a whole composed recipe can be.

    `exclude` is a set of `("food", name)`/`("recipe", name)` pairs to
    skip — how a weekly plan (build_diet_plan's own docstring) avoids
    suggesting the same breakfast every single day: the caller passes
    in whatever it already picked for this meal slot on earlier days.
    Keyed by name rather than pk deliberately: two separate `Food` rows
    that both ended up named "Nutella" (found live — an easy way for a
    shared library to end up with near-duplicates, e.g. imported from
    OFF twice under slightly different barcodes) are indistinguishable
    to whoever's looking at the suggested plan, so excluding only one
    row's pk would still let the "variety" look like it repeated
    anyway. Neither this nor the meal_slot filter above is applied if
    it would rule out every candidate, so a thin library still always
    gets *a* suggestion rather than none — a repeat, or an
    off-topic-but-edible suggestion, is strictly better than an empty
    meal."""
    candidates = []
    for food in Food.objects.filter(Q(owner=user) | Q(owner__isnull=True), active=True):
        if food.calories > 0:
            candidates.append(("food", food, Decimal(food.calories)))
    recipe_qs = Recipe.objects.filter(Q(owner=user) | Q(owner__isnull=True))
    for recipe in recipe_qs:
        per_serving_calories = services.recipe_per_serving_nutrition(recipe).calories
        if per_serving_calories > 0:
            candidates.append(("recipe", recipe, per_serving_calories))

    if not candidates:
        return None

    if meal_slot is not None:
        on_topic = [
            c for c in candidates if c[0] == "food" or c[1].meal_slot_id in (None, meal_slot.pk)
        ]
        if on_topic:
            candidates = on_topic

    if exclude:
        remaining = [c for c in candidates if (c[0], c[1].name) not in exclude]
        if remaining:
            candidates = remaining

    kind, item, natural_calories = min(candidates, key=lambda c: abs(c[2] - target_calories))
    scale_factor = target_calories / natural_calories

    if kind == "food":
        quantity = (item.serving_size * scale_factor).quantize(Decimal("0.01"))
        return SuggestedItem(food=item, recipe=None, quantity=quantity)
    quantity = scale_factor.quantize(Decimal("0.01"))
    return SuggestedItem(food=None, recipe=item, quantity=quantity)


def split_calories_evenly(total_calories: int, meal_count: int) -> list[int]:
    """`total_calories` split into `meal_count` whole-kcal shares that
    always sum back to exactly `total_calories` — the last share
    absorbs whatever integer-division remainder the others left, so
    nothing is lost or invented. An even split, not a hardcoded
    "breakfast is 25%" assumption — the user can adjust any one meal's
    share afterward by editing that DietPlanMeal directly."""
    if meal_count <= 0:
        return []
    base = total_calories // meal_count
    shares = [base] * meal_count
    shares[-1] += total_calories - base * meal_count
    return shares


# 0=Monday..6=Sunday — matches Python's own date.weekday(), which
# apply_diet_plan below uses to pick a weekly plan's meals for a given
# date. Not a real "week starts Monday" opinion; just the same
# zero-based numbering the standard library already uses, so the two
# never need translating between two different conventions.
WEEKDAYS = range(7)


@transaction.atomic
def build_diet_plan(
    user,
    *,
    name,
    goal,
    target_calories,
    target_protein_grams,
    target_carbohydrate_grams,
    target_fat_grams,
    meal_slots,
    is_weekly=False,
):
    """Creates a new active DietPlan (deactivating any previous one —
    old plans are kept, not deleted, docs/NUTRITION.md "DietPlan"),
    splits the calorie target evenly across `meal_slots`, and suggests
    one item per meal via `suggest_item_for_calorie_budget`. A meal
    with no suggestion available (nothing in the user's library yet)
    is still created, just with zero items — the user fills it in
    manually rather than the whole plan failing to generate.

    `is_weekly=False` (the default) generates exactly one day's worth
    of meals, applied to whatever date apply_diet_plan is later given
    — the original, still-default behavior.

    `is_weekly=True` generates one day's worth *per weekday* instead,
    each hitting the same daily calorie/macro target as any other day
    (this plan's own target_calories/protein/carb/fat stay the daily
    figures throughout, never a weekly total) — the variety a single
    repeating day can't offer comes from *which* item fills each meal,
    not from how much of it: `suggest_item_for_calorie_budget` is told
    to skip whatever it already suggested for that same meal slot on
    an earlier day this week (so a week's breakfasts differ from each
    other) *and* whatever it already suggested for a different meal
    slot on this same day (so breakfast, lunch, and dinner aren't the
    same thing on repeat just because splitting one calorie target
    evenly across several meals gives them all the same budget) — as
    long as the library has more than one reasonable option, without
    ever drifting the day's own calorie/macro numbers away from the
    real target to manufacture that variety."""
    DietPlan.objects.filter(user=user, is_active=True).update(is_active=False)
    plan = DietPlan.objects.create(
        user=user,
        name=name,
        goal=goal,
        target_calories=target_calories,
        target_protein_grams=target_protein_grams,
        target_carbohydrate_grams=target_carbohydrate_grams,
        target_fat_grams=target_fat_grams,
        is_active=True,
        is_weekly=is_weekly,
    )
    shares = split_calories_evenly(target_calories, len(meal_slots))
    weekdays = WEEKDAYS if is_weekly else [None]
    # Tracks, per meal slot, every food/recipe already used for it on
    # an earlier day this week — the exclusion set
    # suggest_item_for_calorie_budget's own `exclude` param needs to
    # avoid repeating a meal slot's suggestion day after day. Stays
    # permanently empty (and inert) for a one-day plan.
    used_by_slot = {meal_slot.pk: set() for meal_slot in meal_slots}
    for weekday in weekdays:
        used_today = set()
        for order, (meal_slot, share) in enumerate(zip(meal_slots, shares, strict=True)):
            diet_plan_meal = DietPlanMeal.objects.create(
                diet_plan=plan,
                meal_slot=meal_slot,
                target_calories=share,
                order=order,
                weekday=weekday,
            )
            suggestion = suggest_item_for_calorie_budget(
                user,
                Decimal(share),
                exclude=used_by_slot[meal_slot.pk] | used_today,
                meal_slot=meal_slot,
            )
            if suggestion is not None:
                DietPlanItem.objects.create(
                    diet_plan_meal=diet_plan_meal,
                    food=suggestion.food,
                    recipe=suggestion.recipe,
                    quantity=suggestion.quantity,
                )
                used_key = (
                    ("food", suggestion.food.name)
                    if suggestion.food
                    else ("recipe", suggestion.recipe.name)
                )
                used_by_slot[meal_slot.pk].add(used_key)
                used_today.add(used_key)
    return plan


def apply_diet_plan(plan, target_date):
    """Materializes every DietPlanItem into a real DiaryEntry for
    `target_date` — the plan itself is never mutated by this, so it
    can be reused across many days (docs/NUTRITION.md "DietPlan").
    For a weekly plan (plan.is_weekly), only `target_date`'s own
    weekday's meals apply — a Monday only ever logs what the plan
    built for Monday, never the whole week at once."""
    from .models import DiaryEntry

    meals = plan.meals.select_related("meal_slot").prefetch_related("items")
    if plan.is_weekly:
        meals = meals.filter(weekday=target_date.weekday())

    created = []
    for diet_plan_meal in meals:
        for item in diet_plan_meal.items.all():
            created.append(
                DiaryEntry.objects.create(
                    user=plan.user,
                    date=target_date,
                    meal_slot=diet_plan_meal.meal_slot,
                    food=item.food,
                    recipe=item.recipe,
                    quantity=item.quantity,
                )
            )
    return created
