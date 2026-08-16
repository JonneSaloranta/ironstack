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


def suggest_item_for_calorie_budget(user, target_calories: Decimal) -> SuggestedItem | None:
    """The single food/recipe from `user`'s own library (+ shared
    foods) whose natural, unscaled calorie figure is closest to
    `target_calories`, then scaled to hit that budget exactly. `None`
    if the user has no foods or recipes to suggest from yet."""
    candidates = []
    for food in Food.objects.filter(Q(owner=user) | Q(owner__isnull=True), active=True):
        if food.calories > 0:
            candidates.append(("food", food, Decimal(food.calories)))
    for recipe in Recipe.objects.filter(owner=user):
        per_serving_calories = services.recipe_per_serving_nutrition(recipe).calories
        if per_serving_calories > 0:
            candidates.append(("recipe", recipe, per_serving_calories))

    if not candidates:
        return None

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
):
    """Creates a new active DietPlan (deactivating any previous one —
    old plans are kept, not deleted, docs/NUTRITION.md "DietPlan"),
    splits the calorie target evenly across `meal_slots`, and suggests
    one item per meal via `suggest_item_for_calorie_budget`. A meal
    with no suggestion available (nothing in the user's library yet)
    is still created, just with zero items — the user fills it in
    manually rather than the whole plan failing to generate."""
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
    )
    shares = split_calories_evenly(target_calories, len(meal_slots))
    for order, (meal_slot, share) in enumerate(zip(meal_slots, shares, strict=True)):
        diet_plan_meal = DietPlanMeal.objects.create(
            diet_plan=plan, meal_slot=meal_slot, target_calories=share, order=order
        )
        suggestion = suggest_item_for_calorie_budget(user, Decimal(share))
        if suggestion is not None:
            DietPlanItem.objects.create(
                diet_plan_meal=diet_plan_meal,
                food=suggestion.food,
                recipe=suggestion.recipe,
                quantity=suggestion.quantity,
            )
    return plan


def apply_diet_plan(plan, target_date):
    """Materializes every DietPlanItem into a real DiaryEntry for
    `target_date` — the plan itself is never mutated by this, so it
    can be reused across many days (docs/NUTRITION.md "DietPlan")."""
    from .models import DiaryEntry

    created = []
    for diet_plan_meal in plan.meals.select_related("meal_slot").prefetch_related("items"):
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
