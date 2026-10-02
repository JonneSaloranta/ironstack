"""A diet plan's shopping list (docs/NUTRITION.md "Shopping list").

A plan's shopping days (ShoppingDay) split the week into trips: each trip
starts on a shopping day and covers the days up to the next one. Whether
the shopping day's own meals belong to that trip (you shop before eating)
or to the previous one (you shop after that day is covered) is the plan's
`shopping_includes_shopping_day`. No shopping days means one trip for the
whole week.

The list for a trip adds up every food the covered days' meals need —
recipes broken down into their ingredients for the planned servings — so
the same food used on several days appears once, as a total, with the
per-day amounts beside it.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

WEEK = tuple(range(7))  # 0=Monday .. 6=Sunday, date.weekday()


@dataclass(frozen=True)
class Trip:
    weekday: int | None  # None: no shopping days set — one trip for the week
    days: tuple[int, ...]  # the weekdays whose meals this trip buys for


def shopping_trips(shopping_weekdays, *, include_shopping_day=True):
    """The trips a set of shopping weekdays makes, in weekday order.

    >>> [t.days for t in shopping_trips([0, 3])]
    [(0, 1, 2), (3, 4, 5, 6)]
    >>> [t.days for t in shopping_trips([0, 3], include_shopping_day=False)]
    [(1, 2, 3), (4, 5, 6, 0)]
    """
    days = sorted(set(shopping_weekdays))
    if not days:
        return [Trip(None, WEEK)]
    trips = []
    for index, day in enumerate(days):
        following = days[(index + 1) % len(days)]
        span = (following - day) % 7 or 7  # one shopping day: the whole week
        offsets = range(span) if include_shopping_day else range(1, span + 1)
        trips.append(Trip(day, tuple((day + offset) % 7 for offset in offsets)))
    return trips


def trip_for_today(trips, today_weekday):
    """The trip whose shopping day is today or comes next — the list a
    user opening the page most likely wants."""
    if trips[0].weekday is None:
        return trips[0]
    return min(trips, key=lambda trip: (trip.weekday - today_weekday) % 7)


@dataclass
class ShoppingRow:
    food: object
    unit: str
    total: Decimal = Decimal("0")
    by_day: dict = field(default_factory=lambda: defaultdict(lambda: Decimal("0")))
    # [(weekday, quantity), ...] in the trip's own day order.
    ordered_days: list = field(default_factory=list)


def _needed_per_day(plan):
    """{weekday: [(food, quantity), ...]} for every weekday the plan
    covers. A one-day plan (meals without a weekday) is the same every
    day; a weekly plan's day only has its own meals."""
    from .models import RecipeIngredient

    meals = list(
        plan.meals.prefetch_related("items__food", "items__recipe").order_by("weekday", "order")
    )
    recipe_ids = {item.recipe_id for meal in meals for item in meal.items.all() if item.recipe_id}
    ingredients = defaultdict(list)
    for ingredient in RecipeIngredient.objects.filter(recipe_id__in=recipe_ids).select_related(
        "food", "recipe"
    ):
        ingredients[ingredient.recipe_id].append(ingredient)

    def foods_of(meal):
        for item in meal.items.all():
            if item.food_id:
                yield item.food, item.quantity
            else:
                servings = Decimal(item.recipe.servings or 1)
                for ingredient in ingredients[item.recipe_id]:
                    yield ingredient.food, ingredient.quantity * item.quantity / servings

    per_day = {day: [] for day in WEEK}
    for meal in meals:
        days = WEEK if meal.weekday is None else (meal.weekday,)
        needed = list(foods_of(meal))
        for day in days:
            per_day[day].extend(needed)
    return per_day


def shopping_list(plan, trip):
    """The trip's foods, each once with its total and per-day amounts,
    sorted by name. Quantities are in the food's own serving unit."""
    per_day = _needed_per_day(plan)
    rows = {}
    for day in trip.days:
        for food, quantity in per_day[day]:
            row = rows.get(food.pk)
            if row is None:
                row = rows[food.pk] = ShoppingRow(food=food, unit=food.serving_unit)
            row.total += quantity
            row.by_day[day] += quantity
    for row in rows.values():
        # Keep the trip's own day order (e.g. Thu, Fri, ..., Mon), not
        # plain weekday order, so the breakdown reads like the week ahead.
        row.ordered_days = [(day, row.by_day[day]) for day in trip.days if day in row.by_day]
    return sorted(rows.values(), key=lambda row: row.food.name.casefold())


def plan_trips(plan):
    """`shopping_trips` for a plan's own saved settings."""
    return shopping_trips(
        plan.shopping_days.values_list("weekday", flat=True),
        include_shopping_day=plan.shopping_includes_shopping_day,
    )


def pick_trip(trips, requested_weekday, today_weekday):
    """The trip starting on `requested_weekday`, or None if no trip does;
    with no request, the one shopped today or next."""
    if requested_weekday is None:
        return trip_for_today(trips, today_weekday)
    return next((trip for trip in trips if trip.weekday == requested_weekday), None)


def set_shopping_settings(plan, weekdays, include_shopping_day):
    """Replace a plan's shopping days and include/exclude choice — the one
    place both the web page and the API write them."""
    from .models import ShoppingDay

    plan.shopping_days.all().delete()
    ShoppingDay.objects.bulk_create(
        ShoppingDay(diet_plan=plan, weekday=day) for day in sorted(set(weekdays))
    )
    plan.shopping_includes_shopping_day = include_shopping_day
    plan.save(update_fields=["shopping_includes_shopping_day", "updated_at"])
