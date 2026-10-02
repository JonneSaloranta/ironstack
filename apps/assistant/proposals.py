"""Assistant proposals: validating what the model suggests, and turning an
accepted suggestion into a real DietPlan/Program — see docs/ASSISTANT.md
"Proposals".

Validation happens twice: when the model proposes (so it can fix its own
mistakes from the error message) and again when the user accepts (their
library may have changed meanwhile). The stored payload is the normalized
validator output, which the same validator accepts again unchanged. Names
in it (meal slots, exercises, recipes) are the stored, canonical ones —
the card translates them when it's rendered (`translate_content`), so it
follows the user's language rather than whatever was active when the
model proposed.

Accepting reuses the domain services that already create these objects
— `apps.programs.services.import_program` and
`apps.nutrition.services.create_diet_plan` — so an assistant-made plan is
exactly as valid, and as editable, as a hand-made or imported one. Both
land inactive: the user decides whether to start using it.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext

from .models import AssistantProposal, ProposalKind, ProposalStatus

DIET_PLAN = ProposalKind.DIET_PLAN
PROGRAM = ProposalKind.PROGRAM

PROGRESSION_METHODS = [
    "double_progression",
    "linear",
    "percentage_based",
    "rpe_rir",
    "rep_range",
    "maintenance",
    "manual",
]

MAX_MEALS = 70
MAX_ITEMS_PER_MEAL = 12
MAX_WORKOUTS = 14
MAX_PRESCRIPTIONS = 20


class ProposalError(Exception):
    """The proposal can't be used as given. The message is written for the
    model (English, specific) when proposing; on accept it's shown to the
    user wrapped in a translated explanation."""


def _decimal(value, field, *, minimum, maximum, required=True):
    if value is None:
        if required:
            raise ProposalError(f"`{field}` is required.")
        return None
    if isinstance(value, bool):
        raise ProposalError(f"`{field}` must be a number.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ProposalError(f"`{field}` must be a number.") from None
    if not number.is_finite() or not (minimum <= number <= maximum):
        raise ProposalError(f"`{field}` must be between {minimum} and {maximum}.")
    return number


def _integer(value, field, *, minimum, maximum, required=True):
    number = _decimal(value, field, minimum=minimum, maximum=maximum, required=required)
    if number is None:
        return None
    if number != number.to_integral_value():
        raise ProposalError(f"`{field}` must be a whole number.")
    return int(number)


def _text(value, field, *, max_length, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ProposalError(f"`{field}` must be a string.")
    value = value.strip()
    if required and not value:
        raise ProposalError(f"`{field}` is required.")
    if len(value) > max_length:
        raise ProposalError(f"`{field}` is longer than {max_length} characters.")
    return value


def _list(value, field, *, minimum, maximum):
    if not isinstance(value, list):
        raise ProposalError(f"`{field}` must be a list.")
    if not (minimum <= len(value) <= maximum):
        raise ProposalError(f"`{field}` must have {minimum}-{maximum} entries.")
    return value


def _dict(value, field):
    if not isinstance(value, dict):
        raise ProposalError(f"`{field}` must be an object.")
    return value


# --- Diet plans -----------------------------------------------------------


def _resolve_meal_slot(user, name):
    from apps.nutrition import services as nutrition_services

    slots = list(nutrition_services.visible_meal_slots(user))
    wanted = name.casefold()
    for slot in slots:
        if wanted in (slot.name.casefold(), gettext(slot.name).casefold()):
            return slot
    available = ", ".join(slot.name for slot in slots)
    raise ProposalError(f"Unknown meal slot “{name}”. Use one of: {available}.")


def _validate_new_food(data, where):
    data = _dict(data, f"{where}.new_food")
    food = {
        "name": _text(data.get("name"), f"{where}.new_food.name", max_length=200, required=True),
        "serving_size": _decimal(
            data.get("serving_size"), f"{where}.new_food.serving_size", minimum=1, maximum=1000
        ),
        "serving_unit": data.get("serving_unit"),
        "calories": _integer(
            data.get("calories"), f"{where}.new_food.calories", minimum=0, maximum=2000
        ),
        "protein_grams": _decimal(
            data.get("protein_grams"), f"{where}.new_food.protein_grams", minimum=0, maximum=1000
        ),
        "carbohydrate_grams": _decimal(
            data.get("carbohydrate_grams"),
            f"{where}.new_food.carbohydrate_grams",
            minimum=0,
            maximum=1000,
        ),
        "fat_grams": _decimal(
            data.get("fat_grams"), f"{where}.new_food.fat_grams", minimum=0, maximum=1000
        ),
    }
    if food["serving_unit"] not in ("g", "ml", "piece"):
        raise ProposalError(f"`{where}.new_food.serving_unit` must be g, ml or piece.")
    # The cheapest guard against invented numbers: macros must roughly add
    # up to the stated calories (4/4/9 kcal per gram).
    from_macros = 4 * food["protein_grams"] + 4 * food["carbohydrate_grams"] + 9 * food["fat_grams"]
    if food["calories"] >= 40 and abs(from_macros - food["calories"]) > food["calories"] * Decimal(
        "0.35"
    ):
        raise ProposalError(
            f"`{where}.new_food` calories ({food['calories']}) don't match its macros "
            f"(≈{int(from_macros)} kcal). Check the values."
        )
    return food


def _resolve_item(user, data, where):
    """Returns (normalized item dict, Food|None, Recipe|None, calories)."""
    from apps.nutrition import services as nutrition_services
    from apps.nutrition.models import Food, Recipe

    data = _dict(data, where)
    sources = [key for key in ("food_id", "recipe_id", "new_food") if data.get(key) is not None]
    if len(sources) != 1:
        raise ProposalError(f"`{where}` needs exactly one of food_id, recipe_id or new_food.")
    source = sources[0]
    if source == "recipe_id":
        quantity = _decimal(
            data.get("quantity"), f"{where}.quantity", minimum=Decimal("0.1"), maximum=20
        )
        recipe = (
            Recipe.objects.filter(
                Q(owner=user) | Q(owner__isnull=True), pk=data["recipe_id"]
            ).first()
            if isinstance(data["recipe_id"], int)
            else None
        )
        if recipe is None:
            raise ProposalError(f"`{where}.recipe_id` isn't a recipe this user can see.")
        calories = nutrition_services.recipe_per_serving_nutrition(recipe).calories * quantity
        item = {"recipe_id": recipe.pk, "quantity": str(quantity), "label": recipe.name}
        return item, None, recipe, calories
    quantity = _decimal(
        data.get("quantity"), f"{where}.quantity", minimum=Decimal("0.1"), maximum=5000
    )
    if source == "food_id":
        food = (
            Food.objects.filter(
                Q(owner=user) | Q(owner__isnull=True), active=True, pk=data["food_id"]
            ).first()
            if isinstance(data["food_id"], int)
            else None
        )
        if food is None:
            raise ProposalError(f"`{where}.food_id` isn't a food this user can see.")
        calories = nutrition_services.scale_nutrition(food, quantity).calories
        item = {
            "food_id": food.pk,
            "quantity": str(quantity),
            "unit": food.serving_unit,
            "label": food.name,
        }
        return item, food, None, calories
    new_food = _validate_new_food(data["new_food"], where)
    calories = Decimal(new_food["calories"]) * quantity / new_food["serving_size"]
    item = {
        "new_food": {key: str(value) for key, value in new_food.items()},
        "quantity": str(quantity),
        "unit": new_food["serving_unit"],
        "label": new_food["name"],
    }
    return item, None, None, calories


def _diet_plan(user, data):
    """Validates and resolves; returns (normalized payload, resolved meals)."""
    data = _dict(data, "proposal")
    is_weekly = bool(data.get("is_weekly", False))
    payload = {
        "name": _text(data.get("name"), "name", max_length=200, required=True),
        "summary": _text(data.get("summary"), "summary", max_length=1500),
        "target_calories": _integer(
            data.get("target_calories"), "target_calories", minimum=800, maximum=8000
        ),
        "is_weekly": is_weekly,
        "meals": [],
    }
    for key in ("target_protein_grams", "target_carbohydrate_grams", "target_fat_grams"):
        payload[key] = str(_decimal(data.get(key), key, minimum=0, maximum=1500))

    resolved_meals = []
    total_calories = Decimal("0")
    weekdays_seen = set()
    for meal_index, meal_data in enumerate(
        _list(data.get("meals"), "meals", minimum=1, maximum=MAX_MEALS)
    ):
        where = f"meals[{meal_index}]"
        meal_data = _dict(meal_data, where)
        slot = _resolve_meal_slot(
            user,
            _text(meal_data.get("meal_slot"), f"{where}.meal_slot", max_length=50, required=True),
        )
        weekday = None
        if is_weekly:
            weekday = _integer(meal_data.get("weekday"), f"{where}.weekday", minimum=0, maximum=6)
            weekdays_seen.add(weekday)
        meal_calories = Decimal("0")
        items, resolved_items = [], []
        for item_index, item_data in enumerate(
            _list(meal_data.get("items"), f"{where}.items", minimum=0, maximum=MAX_ITEMS_PER_MEAL)
        ):
            item, food, recipe, calories = _resolve_item(
                user, item_data, f"{where}.items[{item_index}]"
            )
            item["calories"] = int(round(calories))
            items.append(item)
            resolved_items.append(
                {
                    "food": food,
                    "recipe": recipe,
                    "new_food": item.get("new_food"),
                    "quantity": Decimal(item["quantity"]),
                }
            )
            meal_calories += calories
        total_calories += meal_calories
        payload["meals"].append(
            {
                "meal_slot": slot.name,
                "weekday": weekday,
                "calories": int(round(meal_calories)),
                "items": items,
            }
        )
        resolved_meals.append(
            {
                "meal_slot": slot,
                "weekday": weekday,
                "target_calories": int(round(meal_calories)),
                "items": resolved_items,
            }
        )
    days = len(weekdays_seen) if is_weekly else 1
    payload["computed_daily_calories"] = int(round(total_calories / max(days, 1)))
    return payload, resolved_meals


def validate_diet_plan(user, data):
    return _diet_plan(user, data)[0]


def _create_food(user, new_food):
    from apps.nutrition.models import Food

    calories = int(Decimal(new_food["calories"]))
    existing = Food.objects.filter(
        owner=user, name=new_food["name"], calories=calories, active=True
    ).first()
    if existing is not None:
        return existing
    return Food.objects.create(
        owner=user,
        name=new_food["name"],
        serving_size=Decimal(new_food["serving_size"]),
        serving_unit=new_food["serving_unit"],
        calories=calories,
        protein_grams=Decimal(new_food["protein_grams"]),
        carbohydrate_grams=Decimal(new_food["carbohydrate_grams"]),
        fat_grams=Decimal(new_food["fat_grams"]),
    )


def _apply_diet_plan(user, payload):
    from apps.nutrition import services as nutrition_services

    payload, meals = _diet_plan(user, payload)
    for meal in meals:
        for item in meal["items"]:
            if item["new_food"]:
                item["food"] = _create_food(user, item["new_food"])
    return nutrition_services.create_diet_plan(
        user,
        name=payload["name"],
        target_calories=payload["target_calories"],
        target_protein_grams=Decimal(payload["target_protein_grams"]),
        target_carbohydrate_grams=Decimal(payload["target_carbohydrate_grams"]),
        target_fat_grams=Decimal(payload["target_fat_grams"]),
        is_weekly=payload["is_weekly"],
        meals=meals,
    )


# --- Programs -------------------------------------------------------------


def _program(user, data):
    """Validates; returns (normalized payload, {exercise_id: Exercise})."""
    from apps.exercises import services as exercise_services

    data = _dict(data, "proposal")
    payload = {
        "name": _text(data.get("name"), "name", max_length=100, required=True),
        "description": _text(data.get("description"), "description", max_length=2000),
        "summary": _text(data.get("summary"), "summary", max_length=1500),
        "workouts": [],
    }
    exercises = {}
    visible = exercise_services.visible_to(user)
    for workout_index, workout_data in enumerate(
        _list(data.get("workouts"), "workouts", minimum=1, maximum=MAX_WORKOUTS)
    ):
        where = f"workouts[{workout_index}]"
        workout_data = _dict(workout_data, where)
        workout = {
            "name": _text(workout_data.get("name"), f"{where}.name", max_length=100, required=True),
            "scheduled_weekday": _integer(
                workout_data.get("scheduled_weekday"),
                f"{where}.scheduled_weekday",
                minimum=0,
                maximum=6,
                required=False,
            ),
            "notes": _text(workout_data.get("notes"), f"{where}.notes", max_length=1000),
            "prescriptions": [],
        }
        for index, item in enumerate(
            _list(
                workout_data.get("prescriptions"),
                f"{where}.prescriptions",
                minimum=1,
                maximum=MAX_PRESCRIPTIONS,
            )
        ):
            item_where = f"{where}.prescriptions[{index}]"
            item = _dict(item, item_where)
            exercise_id = item.get("exercise_id")
            exercise = (
                visible.filter(pk=exercise_id).first() if isinstance(exercise_id, int) else None
            )
            if exercise is None:
                raise ProposalError(
                    f"`{item_where}.exercise_id` isn't an exercise this user can see — "
                    "use an id from search_exercises."
                )
            exercises[exercise.pk] = exercise
            min_reps = _integer(
                item.get("min_reps"), f"{item_where}.min_reps", minimum=1, maximum=100
            )
            max_reps = _integer(
                item.get("max_reps"), f"{item_where}.max_reps", minimum=1, maximum=100
            )
            if min_reps > max_reps:
                raise ProposalError(f"`{item_where}`: min_reps is greater than max_reps.")
            method = item.get("progression_method") or "double_progression"
            if method not in PROGRESSION_METHODS:
                raise ProposalError(
                    f"`{item_where}.progression_method` must be one of "
                    f"{', '.join(PROGRESSION_METHODS)}."
                )
            target_weight = _decimal(
                item.get("target_weight_kg"),
                f"{item_where}.target_weight_kg",
                minimum=0,
                maximum=1000,
                required=False,
            )
            target_rpe = _decimal(
                item.get("target_rpe"),
                f"{item_where}.target_rpe",
                minimum=1,
                maximum=10,
                required=False,
            )
            increment = _decimal(
                item.get("weight_increment_kg"),
                f"{item_where}.weight_increment_kg",
                minimum=0,
                maximum=50,
                required=False,
            )
            workout["prescriptions"].append(
                {
                    "exercise_id": exercise.pk,
                    "exercise_label": exercise.name,
                    "set_count": _integer(
                        item.get("set_count"), f"{item_where}.set_count", minimum=1, maximum=20
                    ),
                    "min_reps": min_reps,
                    "max_reps": max_reps,
                    "target_weight_kg": str(target_weight) if target_weight is not None else None,
                    "target_rpe": str(target_rpe) if target_rpe is not None else None,
                    "progression_method": method,
                    "weight_increment_kg": str(increment) if increment is not None else None,
                    "notes": _text(item.get("notes"), f"{item_where}.notes", max_length=500),
                }
            )
        payload["workouts"].append(workout)
    return payload, exercises


def validate_program(user, data):
    return _program(user, data)[0]


def _apply_program(user, payload):
    from apps.programs import services as program_services

    payload, exercises = _program(user, payload)
    import_payload = {
        "name": payload["name"],
        "description": payload["description"],
        "workouts": [
            {
                "name": workout["name"],
                "order": order,
                "scheduled_weekday": workout["scheduled_weekday"],
                "notes": workout["notes"],
                "prescriptions": [
                    {
                        **{key: value for key, value in item.items() if key != "exercise_label"},
                        "order": item_order,
                        # import_program resolves exercises by natural key:
                        # a system one by name, a custom one among `user`'s own.
                        "exercise": {
                            "name": exercises[item["exercise_id"]].name,
                            "is_system": exercises[item["exercise_id"]].owner_id is None,
                        },
                    }
                    for item_order, item in enumerate(workout["prescriptions"])
                ],
            }
            for order, workout in enumerate(payload["workouts"])
        ],
    }
    return program_services.import_program(user, import_payload)


# --- Lifecycle ------------------------------------------------------------


def record(message, kind, payload):
    return AssistantProposal.objects.create(
        message=message, kind=kind, title=payload["name"], payload=payload
    )


@transaction.atomic
def accept(proposal):
    """Creates the proposed object for the proposal's owner and returns it.
    Raises ProposalError if it can no longer be created as proposed."""
    proposal = AssistantProposal.objects.select_for_update().get(pk=proposal.pk)
    if proposal.status != ProposalStatus.PENDING:
        raise ProposalError("This proposal has already been handled.")
    user = proposal.message.conversation.user
    if proposal.kind == DIET_PLAN:
        created = _apply_diet_plan(user, proposal.payload)
    else:
        created = _apply_program(user, proposal.payload)
    proposal.status = ProposalStatus.ACCEPTED
    proposal.created_object_id = created.pk
    proposal.save(update_fields=["status", "created_object_id", "updated_at"])
    return created


def dismiss(proposal):
    if proposal.status == ProposalStatus.PENDING:
        proposal.status = ProposalStatus.DISMISSED
        proposal.save(update_fields=["status", "updated_at"])
