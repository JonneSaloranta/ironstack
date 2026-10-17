"""The assistant's tools - what the model may look at and propose. See
docs/ASSISTANT.md "Tools".

Every tool runs as one specific user (`ToolContext.user`) and only ever
reads that user's own rows plus the shared/system rows they can already
see in the app (system exercises, shared foods, built-in recipes) -
through the same `visible_to`-style queries the views use. There's no
tool that writes anything except the two `propose_*` tools, which only
record an AssistantProposal for the user to accept or dismiss
(apps.assistant.proposals).

Inputs come from the model and are treated as untrusted: every tool
validates its own arguments and raises ToolInputError, which goes back
to the model as an error result it can correct, never as a crash.

Tool results are compact JSON in canonical units (kg, meters, kcal) -
the system prompt tells the model which units the user prefers to read.
"""

import json
from datetime import timedelta
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from . import proposals


class ToolInputError(Exception):
    """The model called a tool with arguments it should fix."""


class ToolContext:
    def __init__(self, user, message):
        self.user = user
        # The AssistantMessage being written - proposals attach to it.
        self.message = message


def _dumps(data):
    return json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False, separators=(",", ":"))


def _num(value, places=1):
    if value is None:
        return None
    return float(round(Decimal(value), places))


def _int_arg(args, key, default, *, minimum, maximum):
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolInputError(f"`{key}` must be a number.")
    return max(minimum, min(maximum, int(value)))


def _str_arg(args, key, *, required=False, max_length=200):
    value = args.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ToolInputError(f"`{key}` must be a string.")
    value = value.strip()
    if required and not value:
        raise ToolInputError(f"`{key}` is required.")
    return value[:max_length]


def _since(days):
    return timezone.now() - timedelta(days=days)


# --- Read tools -----------------------------------------------------------


def get_user_overview(ctx, args):
    from apps.coaching.models import CoachingRelationship
    from apps.measurements.models import BodyMeasurement
    from apps.nutrition.models import DietPlan, NutritionGoal, NutritionProfile, NutritionTarget

    user = ctx.user
    today = timezone.localdate()
    data = {
        "first_name": user.first_name or None,
        "today": today,
        "weekday": today.strftime("%A"),
        "preferred_units": user.unit_system,
        "height_m": _num(user.height, 3),
        "sections_enabled": {
            "nutrition": user.nutrition_enabled,
            "stretching": user.stretching_enabled,
        },
    }
    weight = (
        BodyMeasurement.objects.filter(
            user=user, measurement_type__name="Body weight", measurement_type__owner__isnull=True
        )
        .order_by("-recorded_at")
        .first()
    )
    if weight is not None:
        data["latest_body_weight_kg"] = _num(weight.value)
        data["latest_body_weight_date"] = weight.recorded_at.date()
    profile = NutritionProfile.objects.filter(user=user).first()
    if profile is not None:
        data["nutrition_profile"] = {
            "biological_sex": profile.biological_sex,
            "age": (
                today.year
                - profile.birth_date.year
                - ((today.month, today.day) < (profile.birth_date.month, profile.birth_date.day))
                if profile.birth_date
                else None
            ),
            "activity_level": profile.activity_level,
            "training_sessions_per_week": profile.training_sessions_per_week,
            "training_session_minutes": profile.training_session_minutes,
            "daily_steps": profile.daily_steps,
        }
    goal = NutritionGoal.objects.filter(user=user, ended_at__isnull=True).first()
    if goal is not None:
        data["nutrition_goal"] = {
            "goal_type": goal.goal_type,
            "target_rate_kg_per_week": _num(goal.target_rate_kg_per_week, 2),
            "target_weight_kg": _num(goal.target_weight),
            "notes": goal.notes,
        }
    target = NutritionTarget.objects.filter(user=user, ended_at__isnull=True).first()
    if target is not None:
        data["daily_nutrition_target"] = {
            "calories": target.daily_calories,
            "protein_g": _num(target.protein_grams),
            "carbohydrate_g": _num(target.carbohydrate_grams),
            "fat_g": _num(target.fat_grams),
        }
    active_plan = DietPlan.objects.filter(user=user, is_active=True).first()
    data["active_diet_plan"] = (
        {"id": active_plan.pk, "name": active_plan.name} if active_plan else None
    )
    data["training_last_4_weeks"] = _training_pattern(user)
    data["active_coaches"] = CoachingRelationship.objects.filter(
        coachee=user, ended_at__isnull=True
    ).count()
    return data


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _training_pattern(user):
    """Which weekdays the user actually trained on lately, and the
    program they last trained from - the closest thing to "their
    current program" (there's no active-program flag)."""
    from collections import Counter

    from apps.workouts.models import WorkoutSession, WorkoutSessionStatus

    completed = WorkoutSession.objects.filter(user=user, status=WorkoutSessionStatus.COMPLETED)
    started = completed.filter(started_at__gte=_since(28)).values_list("started_at", flat=True)
    weekdays = Counter(timezone.localtime(moment).weekday() for moment in started)
    latest = (
        completed.filter(program__isnull=False, started_at__gte=_since(90))
        .select_related("program")
        .order_by("-started_at")
        .first()
    )
    return {
        "sessions": sum(weekdays.values()),
        "sessions_by_weekday": {_WEEKDAYS[day]: weekdays[day] for day in sorted(weekdays)},
        "latest_program": (
            {"id": latest.program_id, "name": latest.program.name} if latest else None
        ),
    }


def get_workout_history(ctx, args):
    from apps.workouts.models import WorkoutSession, WorkoutSessionStatus

    days = _int_arg(args, "days", 30, minimum=1, maximum=365)
    sessions = (
        WorkoutSession.objects.filter(
            user=ctx.user, status=WorkoutSessionStatus.COMPLETED, started_at__gte=_since(days)
        )
        .select_related("workout", "program")
        .prefetch_related("performed_exercises__exercise", "performed_exercises__sets")
        .order_by("-started_at")[:40]
    )
    result = []
    for session in sessions:
        exercises = []
        for performed in session.performed_exercises.all():
            sets = [s for s in performed.sets.all() if not s.is_warmup]
            exercise = {
                "exercise": gettext(performed.display_name),
                "sets": [_set_label(s) for s in sets[:10]],
            }
            if performed.notes:
                exercise["plan_notes"] = performed.notes[:300]
            set_notes = [f"set {s.set_number}: {s.notes[:200]}" for s in sets if s.notes]
            if set_notes:
                exercise["set_notes"] = set_notes[:10]
            exercises.append(exercise)
        result.append(
            {
                "date": timezone.localtime(session.started_at).date(),
                "workout": session.workout.name if session.workout_id else None,
                "program": session.program.name if session.program_id else None,
                "duration_minutes": (
                    int((session.ended_at - session.started_at).total_seconds() // 60)
                    if session.ended_at
                    else None
                ),
                "exercises": exercises,
            }
        )
    return {"days": days, "completed_sessions": result}


def _set_label(exercise_set):
    label = f"{_num(exercise_set.weight)}kg x {exercise_set.reps}"
    if exercise_set.rpe is not None:
        label += f" @RPE{_num(exercise_set.rpe)}"
    if exercise_set.rir is not None:
        label += f" RIR{exercise_set.rir}"
    if exercise_set.is_failure:
        label += " (to failure)"
    return label


def get_personal_records(ctx, args):
    from apps.records.models import PersonalRecord, PRType

    query = _str_arg(args, "exercise_query", max_length=100).lower()
    records = (
        PersonalRecord.objects.filter(
            user=ctx.user, record_type__in=[PRType.MAX_WEIGHT, PRType.ESTIMATED_1RM]
        )
        .select_related("exercise")
        .order_by("-achieved_at")
    )
    best = {}
    for record in records:
        name = gettext(record.exercise.name)
        if query and query not in name.lower() and query not in record.exercise.name.lower():
            continue
        entry = best.setdefault(name, {"exercise": name, "exercise_id": record.exercise_id})
        key = "max_weight" if record.record_type == PRType.MAX_WEIGHT else "estimated_1rm"
        if key not in entry:  # newest first, so the first one seen is current
            entry[key] = {
                "value_kg": _num(record.value),
                "set": f"{_num(record.weight)}kg x {record.reps}",
                "date": record.achieved_at.date(),
            }
        if len(best) >= 50:
            break
    return {"records": list(best.values())}


def get_body_measurements(ctx, args):
    from apps.measurements.models import BodyMeasurement

    days = _int_arg(args, "days", 90, minimum=1, maximum=730)
    readings = (
        BodyMeasurement.objects.filter(user=ctx.user, recorded_at__gte=_since(days))
        .select_related("measurement_type")
        .order_by("measurement_type_id", "-recorded_at")
    )
    by_type = {}
    for reading in readings:
        kind = reading.measurement_type.unit_kind
        entry = by_type.setdefault(
            reading.measurement_type_id,
            {
                "type": gettext(reading.measurement_type.name),
                "unit": {"weight": "kg", "length": "m", "percentage": "%"}.get(kind, kind),
                "readings": [],
            },
        )
        if len(entry["readings"]) < 15:
            entry["readings"].append(
                {"date": reading.recorded_at.date(), "value": _num(reading.value, 3)}
            )
    return {"days": days, "measurements": list(by_type.values())}


def get_activities(ctx, args):
    from apps.activities.models import Activity

    days = _int_arg(args, "days", 30, minimum=1, maximum=365)
    activities = (
        Activity.objects.filter(
            user=ctx.user, date__gte=timezone.localdate() - timedelta(days=days)
        )
        .select_related("activity_type")
        .order_by("-date")[:60]
    )
    return {
        "days": days,
        "activities": [
            {
                "date": activity.date,
                "type": gettext(activity.activity_type.name),
                "duration_minutes": int(activity.duration.total_seconds() // 60),
                "distance_km": _num(activity.distance / 1000, 2) if activity.distance else None,
                "calories": activity.calories,
            }
            for activity in activities
        ],
    }


def get_nutrition_overview(ctx, args):
    from apps.nutrition import services as nutrition_services

    days = _int_arg(args, "days", 14, minimum=1, maximum=90)
    stats = nutrition_services.nutrition_stats(ctx.user, days=days)
    today = timezone.localdate()
    daily = []
    for offset in range(min(days, 14)):
        day = today - timedelta(days=offset)
        totals = nutrition_services.daily_totals(ctx.user, day)
        if totals.calories > 0:
            daily.append(
                {
                    "date": day,
                    "calories": int(totals.calories),
                    "protein_g": _num(totals.protein_grams),
                    "carbohydrate_g": _num(totals.carbohydrate_grams),
                    "fat_g": _num(totals.fat_grams),
                }
            )
    return {
        "days": days,
        "days_logged": stats.days_logged,
        "median_daily": {
            "calories": int(stats.median_calories),
            "protein_g": _num(stats.median_protein_grams),
            "carbohydrate_g": _num(stats.median_carbohydrate_grams),
            "fat_g": _num(stats.median_fat_grams),
        },
        "recent_logged_days": daily,
    }


def _food_details(food):
    """What a food is beyond its macros - allergens and labels above all,
    so a plan never leans on something the user can't or won't eat.
    Mostly Open Food Facts data (blank for hand-entered foods), passed on
    verbatim and only when present."""
    details = {}
    for key, value, limit in (
        ("allergens", food.allergens, 200),
        ("labels", food.labels, 200),
        ("categories", food.categories, 150),
        ("ingredients", food.ingredients_text, 300),
    ):
        if value:
            details[key] = value[:limit]
    if food.nutri_score:
        details["nutri_score"] = food.nutri_score.upper()
    if food.price_amount is not None:
        details["package_price"] = f"{_num(food.price_amount, 2)} {food.price_currency}"
        if food.quantity:
            details["package_size"] = food.quantity
    return details


def _diary_entry_label(entry, name):
    if entry.food_id:
        label = f"{name} {_num(entry.quantity)}{entry.food.serving_unit}"
    elif entry.recipe_id:
        label = f"{name} x{_num(entry.quantity)} servings"
    else:
        label = f"{name} x{_num(entry.quantity)}"
    if entry.notes:
        label += f" (note: {entry.notes[:150]})"
    return label


def get_food_diary(ctx, args):
    """What the user eats, item by item - the favorites a diet plan can
    be built from, plus the last few logged days as they were eaten.
    A food or recipe only carries its id while it's still active, i.e.
    while propose_diet_plan would accept it."""
    from collections import Counter
    from statistics import median

    from apps.nutrition.models import DiaryEntry

    days = _int_arg(args, "days", 60, minimum=1, maximum=365)
    today = timezone.localdate()
    entries = (
        DiaryEntry.objects.filter(
            user=ctx.user, date__gt=today - timedelta(days=days), date__lte=today
        )
        .select_related("food", "recipe", "meal_slot")
        .order_by("-date", "meal_slot__order", "created_at")
    )
    favorites = {}
    recent_days = {}
    for entry in entries:
        if entry.food_id:
            key = ("food", entry.food_id)
            name = entry.food.name
        elif entry.recipe_id:
            key = ("recipe", entry.recipe_id)
            name = gettext(entry.recipe.name)
        else:
            name = entry.quick_name or gettext("Quick entry")
            key = ("quick", name.lower())
        slot = gettext(entry.meal_slot.name)
        favorite = favorites.get(key)
        if favorite is None:
            favorite = favorites[key] = {
                "name": name,
                "dates": set(),
                "quantities": [],
                "slots": Counter(),
                "last_eaten": entry.date,
                "entry": entry,
            }
        favorite["dates"].add(entry.date)
        favorite["quantities"].append(entry.quantity)
        favorite["slots"][slot] += 1
        if entry.date in recent_days or len(recent_days) < 7:
            meals = recent_days.setdefault(entry.date, {})
            meals.setdefault(slot, []).append(_diary_entry_label(entry, name))

    ranked = sorted(
        favorites.values(),
        key=lambda f: (len(f["dates"]), len(f["quantities"]), f["last_eaten"]),
        reverse=True,
    )
    result = []
    for favorite in ranked[:40]:
        entry = favorite["entry"]
        item = {"name": favorite["name"]}
        if entry.food_id:
            item["brand"] = entry.food.brand or None
            item["food_id"] = entry.food_id if entry.food.active else None
            item["typical_quantity"] = (
                f"{_num(median(favorite['quantities']))}{entry.food.serving_unit}"
            )
            item.update(_food_details(entry.food))
        elif entry.recipe_id:
            item["recipe_id"] = entry.recipe_id if entry.recipe.active else None
            item["typical_quantity"] = f"{_num(median(favorite['quantities']))} servings"
        else:
            item["quick_entry"] = True
        item.update(
            {
                "days_eaten": len(favorite["dates"]),
                "times_logged": len(favorite["quantities"]),
                "usual_meals": [slot for slot, _count in favorite["slots"].most_common(3)],
                "last_eaten": favorite["last_eaten"],
            }
        )
        result.append(item)
    return {
        "days": days,
        "days_logged": len({date for f in favorites.values() for date in f["dates"]}),
        "most_eaten": result,
        "recent_days": [{"date": date, "meals": meals} for date, meals in recent_days.items()],
    }


def get_progression_suggestions(ctx, args):
    """The app's own next-session weight suggestions (apps.progression) -
    the same figures the user sees when they log a workout, so advice
    lines up with what the app shows them."""
    from apps.programs import services as program_services
    from apps.progression.suggestions import suggest_weight

    program_id = args.get("program_id")
    if program_id is None:
        latest = _training_pattern(ctx.user)["latest_program"]
        if latest is None:
            raise ToolInputError(
                "The user hasn't trained from a program lately - pass a program_id from "
                "list_programs."
            )
        program_id = latest["id"]
    program = (
        program_services.visible_to(ctx.user).filter(pk=program_id).first()
        if isinstance(program_id, int)
        else None
    )
    if program is None:
        raise ToolInputError("No program with that id is visible to this user.")
    workouts = []
    for workout in program.workouts.prefetch_related("prescriptions__exercise"):
        exercises = []
        for prescription in workout.prescriptions.all()[:20]:
            suggestion = suggest_weight(ctx.user, prescription)
            exercises.append(
                {
                    "exercise": gettext(prescription.exercise.name),
                    "suggested_weight_kg": _num(suggestion.suggested_weight),
                    "reps": f"{suggestion.target_min_reps}-{suggestion.target_max_reps}",
                    "decision": suggestion.action,
                    "confidence": suggestion.confidence,
                    "reason": str(suggestion.reason),
                }
            )
        workouts.append({"workout": workout.name, "exercises": exercises})
    return {"program": {"id": program.pk, "name": program.name}, "workouts": workouts}


def get_calorie_adjustment(ctx, args):
    """apps.nutrition's calorie-adjustment check: the weight trend against
    the goal's target rate, and the change it would suggest."""
    from apps.nutrition.suggestions import suggest_calorie_adjustment

    suggestion = suggest_calorie_adjustment(ctx.user)
    return {
        "status": suggestion.action,
        "target_rate_kg_per_week": _num(suggestion.target_rate_kg_per_week, 2),
        "actual_rate_kg_per_week": _num(suggestion.actual_rate_kg_per_week, 2),
        "suggested_daily_calories": suggestion.suggested_daily_calories,
        "change_kcal_per_day": suggestion.delta_kcal,
        "confidence": suggestion.confidence,
        "reason": str(suggestion.reason),
    }


def get_stretching(ctx, args):
    from apps.stretching import services as stretching_services
    from apps.stretching.models import StretchSessionStatus

    if not ctx.user.stretching_enabled:
        return {"stretching_enabled": False}
    days = _int_arg(args, "days", 30, minimum=1, maximum=365)
    sessions = stretching_services.sessions_for(ctx.user).filter(
        status=StretchSessionStatus.COMPLETED,
        date__gt=timezone.localdate() - timedelta(days=days),
    )[:40]
    routines = stretching_services.visible_routines(ctx.user).filter(owner=ctx.user)[:20]
    return {
        "stretching_enabled": True,
        "days": days,
        "sessions": [
            {
                "date": session.date,
                "name": gettext(session.name) if session.name else None,
                "after_workout": session.after_workout_id is not None,
                "duration_minutes": (
                    int(session.duration.total_seconds() // 60) if session.duration else None
                ),
                **({"notes": session.notes[:200]} if session.notes else {}),
            }
            for session in sessions
        ],
        "own_routines": [routine.name for routine in routines],
    }


def list_programs(ctx, args):
    from apps.programs import services as program_services

    programs = (
        program_services.visible_to(ctx.user)
        .prefetch_related("workouts")
        .order_by("owner_id", "name")
    )
    return {
        "programs": [
            {
                "id": program.pk,
                "name": gettext(program.name) if program.owner_id is None else program.name,
                "kind": (
                    "built-in template"
                    if program.owner_id is None
                    else ("yours" if program.owner_id == ctx.user.pk else "shared by your coach")
                ),
                "workouts": [workout.name for workout in program.workouts.all()],
            }
            for program in programs[:60]
        ]
    }


def get_program(ctx, args):
    from apps.programs import services as program_services

    program_id = args.get("program_id")
    program = (
        program_services.visible_to(ctx.user).filter(pk=program_id).first()
        if isinstance(program_id, int)
        else None
    )
    if program is None:
        raise ToolInputError("No program with that id is visible to this user.")
    return {
        "id": program.pk,
        "name": program.name,
        "description": program.description,
        "workouts": [
            {
                "name": workout.name,
                "scheduled_weekday": workout.scheduled_weekday,
                "prescriptions": [
                    {
                        "exercise": gettext(p.exercise.name),
                        "exercise_id": p.exercise_id,
                        "sets": p.set_count,
                        "reps": f"{p.min_reps}-{p.max_reps}",
                        "target_weight_kg": _num(p.target_weight),
                        "target_rpe": _num(p.target_rpe),
                        "progression_method": p.progression_method,
                    }
                    for p in workout.prescriptions.select_related("exercise")
                ],
            }
            for workout in program.workouts.all()
        ],
    }


def list_diet_plans(ctx, args):
    from apps.nutrition.models import DietPlan

    plans = DietPlan.objects.filter(user=ctx.user).order_by("-is_active", "-created_at")[:30]
    return {
        "diet_plans": [
            {
                "id": plan.pk,
                "name": plan.name,
                "active": plan.is_active,
                "weekly": plan.is_weekly,
                "target_calories": plan.target_calories,
            }
            for plan in plans
        ]
    }


def get_diet_plan(ctx, args):
    from apps.nutrition import services as nutrition_services
    from apps.nutrition.models import DietPlan

    plan_id = args.get("diet_plan_id")
    plan = (
        DietPlan.objects.filter(user=ctx.user, pk=plan_id).first()
        if isinstance(plan_id, int)
        else None
    )
    if plan is None:
        raise ToolInputError("No diet plan with that id belongs to this user.")
    meals = []
    for meal in plan.meals.select_related("meal_slot").prefetch_related(
        "items__food", "items__recipe"
    ):
        items = []
        for item in meal.items.all():
            if item.food_id:
                nutrition = nutrition_services.scale_nutrition(item.food, item.quantity)
                label = f"{item.food.name} {_num(item.quantity)}{item.food.serving_unit}"
            else:
                nutrition = nutrition_services.recipe_per_serving_nutrition(item.recipe).scaled_by(
                    item.quantity
                )
                label = f"{gettext(item.recipe.name)} x{_num(item.quantity)} servings"
            items.append({"item": label, "calories": int(nutrition.calories)})
        meals.append(
            {
                "meal_slot": gettext(meal.meal_slot.name),
                "weekday": meal.weekday,
                "target_calories": meal.target_calories,
                "items": items,
            }
        )
    return {
        "id": plan.pk,
        "name": plan.name,
        "weekly": plan.is_weekly,
        "targets": {
            "calories": plan.target_calories,
            "protein_g": _num(plan.target_protein_grams),
            "carbohydrate_g": _num(plan.target_carbohydrate_grams),
            "fat_g": _num(plan.target_fat_grams),
        },
        "meals": meals,
    }


def search_exercises(ctx, args):
    from apps.exercises import services as exercise_services

    query = _str_arg(args, "query", required=True, max_length=100)
    exercises = exercise_services.search(exercise_services.visible_to(ctx.user), query)
    exercises = exercises.prefetch_related("primary_muscle_groups").order_by("name")[:20]
    return {
        "exercises": [
            {
                "id": exercise.pk,
                "name": gettext(exercise.name),
                "english_name": exercise.name,
                "equipment": gettext(exercise.equipment.name) if exercise.equipment_id else None,
                "primary_muscles": [
                    gettext(group.name) for group in exercise.primary_muscle_groups.all()
                ],
                "movement_type": exercise.movement_type,
                "custom": exercise.owner_id is not None,
            }
            for exercise in exercises
        ]
    }


def search_foods(ctx, args):
    from apps.nutrition.models import Food

    query = _str_arg(args, "query", required=True, max_length=100)
    foods = (
        Food.objects.filter(Q(owner=ctx.user) | Q(owner__isnull=True), active=True)
        .filter(Q(name__icontains=query) | Q(brand__icontains=query))
        .order_by("owner_id", "name")[:20]
    )
    return {
        "foods": [
            {
                "id": food.pk,
                "name": food.name,
                "brand": food.brand or None,
                "per": f"{_num(food.serving_size)}{food.serving_unit}",
                "calories": food.calories,
                "protein_g": _num(food.protein_grams),
                "carbohydrate_g": _num(food.carbohydrate_grams),
                "fat_g": _num(food.fat_grams),
                **_food_details(food),
            }
            for food in foods
        ]
    }


def search_recipes(ctx, args):
    from apps.nutrition import services as nutrition_services
    from apps.nutrition.models import Recipe

    query = _str_arg(args, "query", max_length=100)
    recipes = Recipe.objects.filter(
        Q(owner=ctx.user) | Q(owner__isnull=True), active=True
    ).prefetch_related("meal_slots", "ingredients__food")
    if query:
        lowered = query.lower()
        recipes = [
            recipe
            for recipe in recipes
            if lowered in recipe.name.lower() or lowered in gettext(recipe.name).lower()
        ]
    result = []
    for recipe in list(recipes)[:20]:
        per_serving = nutrition_services.recipe_per_serving_nutrition(recipe)
        result.append(
            {
                "id": recipe.pk,
                "name": gettext(recipe.name),
                "servings": recipe.servings,
                "per_serving": {
                    "calories": int(per_serving.calories),
                    "protein_g": _num(per_serving.protein_grams),
                    "carbohydrate_g": _num(per_serving.carbohydrate_grams),
                    "fat_g": _num(per_serving.fat_grams),
                },
                "meal_slots": [gettext(slot.name) for slot in recipe.meal_slots.all()],
                "ingredients": [
                    f"{ingredient.food.name} {_num(ingredient.quantity)}"
                    f"{ingredient.food.serving_unit}"
                    for ingredient in recipe.ingredients.all()[:20]
                ],
            }
        )
        allergens = sorted({i.food.allergens for i in recipe.ingredients.all() if i.food.allergens})
        if allergens:
            result[-1]["ingredient_allergens"] = ", ".join(allergens)[:300]
    return {"recipes": result}


def list_meal_slots(ctx, args):
    from apps.nutrition import services as nutrition_services

    return {
        "meal_slots": [
            {"name": slot.name, "display_name": gettext(slot.name)}
            for slot in nutrition_services.visible_meal_slots(ctx.user)
        ]
    }


# --- Proposal tools -------------------------------------------------------


def propose_diet_plan(ctx, args):
    try:
        payload = proposals.validate_diet_plan(ctx.user, args)
    except proposals.ProposalError as exc:
        raise ToolInputError(str(exc)) from exc
    proposal = proposals.record(ctx.message, proposals.DIET_PLAN, payload)
    return {
        "proposal_id": proposal.pk,
        "status": "Shown to the user as a card. Nothing is created unless they accept it.",
        "computed_daily_calories": payload["computed_daily_calories"],
    }


def propose_program(ctx, args):
    try:
        payload = proposals.validate_program(ctx.user, args)
    except proposals.ProposalError as exc:
        raise ToolInputError(str(exc)) from exc
    proposal = proposals.record(ctx.message, proposals.PROGRAM, payload)
    return {
        "proposal_id": proposal.pk,
        "status": "Shown to the user as a card. Nothing is created unless they accept it.",
    }


# --- Registry -------------------------------------------------------------

_DAYS = {"type": "integer", "description": "How many days back to look."}

_NEW_FOOD_SCHEMA = {
    "type": "object",
    "description": (
        "Only when no existing food fits: a new food to add to the user's own library, "
        "with typical nutrition values per serving_size."
    ),
    "properties": {
        "name": {"type": "string"},
        "serving_size": {"type": "number", "description": "e.g. 100"},
        "serving_unit": {"type": "string", "enum": ["g", "ml", "piece"]},
        "calories": {"type": "integer", "description": "kcal per serving_size"},
        "protein_grams": {"type": "number"},
        "carbohydrate_grams": {"type": "number"},
        "fat_grams": {"type": "number"},
    },
    "required": [
        "name",
        "serving_size",
        "serving_unit",
        "calories",
        "protein_grams",
        "carbohydrate_grams",
        "fat_grams",
    ],
}

TOOLS = [
    {
        "name": "get_user_overview",
        "description": (
            "The user's profile: today's date, preferred units, height, latest body weight, "
            "nutrition profile, current nutrition goal and daily target, active diet plan, "
            "which weekdays they trained on lately and from which program, and whether "
            "they have a coach."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "handler": get_user_overview,
        "activity": _("Looking at your profile…"),
    },
    {
        "name": "get_workout_history",
        "description": (
            "Completed workout sessions with their working sets (RPE/RIR, sets to "
            "failure) and the user's set notes, newest first."
        ),
        "input_schema": {"type": "object", "properties": {"days": _DAYS}},
        "handler": get_workout_history,
        "activity": _("Looking at your workouts…"),
    },
    {
        "name": "get_personal_records",
        "description": "Current max-weight and estimated-1RM records per exercise.",
        "input_schema": {
            "type": "object",
            "properties": {
                "exercise_query": {
                    "type": "string",
                    "description": "Optional part of an exercise name to filter by.",
                }
            },
        },
        "handler": get_personal_records,
        "activity": _("Looking at your records…"),
    },
    {
        "name": "get_body_measurements",
        "description": "Body measurements (weight, body fat, circumferences) over time.",
        "input_schema": {"type": "object", "properties": {"days": _DAYS}},
        "handler": get_body_measurements,
        "activity": _("Looking at your measurements…"),
    },
    {
        "name": "get_activities",
        "description": "Other logged activities (running, cycling, ...).",
        "input_schema": {"type": "object", "properties": {"days": _DAYS}},
        "handler": get_activities,
        "activity": _("Looking at your activities…"),
    },
    {
        "name": "get_nutrition_overview",
        "description": "What the user has actually eaten: median daily intake and recent days.",
        "input_schema": {"type": "object", "properties": {"days": _DAYS}},
        "handler": get_nutrition_overview,
        "activity": _("Looking at your food diary…"),
    },
    {
        "name": "get_food_diary",
        "description": (
            "Which foods and recipes the user actually eats, most eaten first (typical "
            "portion, usual meals, last eaten), plus their last few logged days item by "
            "item. Use it to build diet plans from food they like; the food_id/recipe_id "
            "work in propose_diet_plan (null when that item is no longer available)."
        ),
        "input_schema": {"type": "object", "properties": {"days": _DAYS}},
        "handler": get_food_diary,
        "activity": _("Looking at your food diary…"),
    },
    {
        "name": "get_calorie_adjustment",
        "description": (
            "The app's own check of the user's weight trend against their nutrition goal, "
            "and the daily calorie change it suggests (on track / adjust / not enough data)."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "handler": get_calorie_adjustment,
        "activity": _("Looking at your weight trend…"),
    },
    {
        "name": "get_stretching",
        "description": "Completed stretching sessions and the user's own stretching routines.",
        "input_schema": {"type": "object", "properties": {"days": _DAYS}},
        "handler": get_stretching,
        "activity": _("Looking at your stretching…"),
    },
    {
        "name": "list_programs",
        "description": "Workout programs the user can use: their own, coach-shared, built-in.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": list_programs,
        "activity": _("Looking at your programs…"),
    },
    {
        "name": "get_program",
        "description": "One program's workouts and exercise prescriptions.",
        "input_schema": {
            "type": "object",
            "properties": {"program_id": {"type": "integer"}},
            "required": ["program_id"],
        },
        "handler": get_program,
        "activity": _("Looking at your programs…"),
    },
    {
        "name": "get_progression_suggestions",
        "description": (
            "The app's own suggested weight for each exercise's next session in a program, "
            "with its reason - the same suggestions the user sees while logging. Defaults "
            "to the program they last trained from."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"program_id": {"type": "integer"}},
        },
        "handler": get_progression_suggestions,
        "activity": _("Looking at your progression…"),
    },
    {
        "name": "list_diet_plans",
        "description": "The user's diet plans.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": list_diet_plans,
        "activity": _("Looking at your diet plans…"),
    },
    {
        "name": "get_diet_plan",
        "description": "One diet plan's targets, meals and items.",
        "input_schema": {
            "type": "object",
            "properties": {"diet_plan_id": {"type": "integer"}},
            "required": ["diet_plan_id"],
        },
        "handler": get_diet_plan,
        "activity": _("Looking at your diet plans…"),
    },
    {
        "name": "search_exercises",
        "description": (
            "Search the exercise library (system + the user's own) by name, in English or "
            "the user's language. Use the returned ids in propose_program."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "handler": search_exercises,
        "activity": _("Searching exercises…"),
    },
    {
        "name": "search_foods",
        "description": (
            "Search foods in the user's library by name or brand. Nutrition is per the "
            "food's own serving size; allergens, labels and ingredients when known. Use "
            "the returned ids in propose_diet_plan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "handler": search_foods,
        "activity": _("Searching foods…"),
    },
    {
        "name": "search_recipes",
        "description": (
            "Search the user's and built-in recipes, with their ingredients and the "
            "ingredients' allergens. Empty query lists them all."
        ),
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "handler": search_recipes,
        "activity": _("Searching recipes…"),
    },
    {
        "name": "list_meal_slots",
        "description": "The meal slots (Breakfast, Lunch, ...) a diet plan meal can use.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": list_meal_slots,
        "activity": _("Looking at your meals…"),
    },
    {
        "name": "propose_diet_plan",
        "description": (
            "Show the user a diet plan they can create with one click. Nothing is saved "
            "unless they accept. Prefer existing foods (search_foods) and recipes "
            "(search_recipes); use new_food only when nothing fits. Food quantities are in "
            "the food's own serving unit (g/ml/piece); recipe quantities are servings. "
            "For a weekly plan give every meal a weekday 0-6 (0 = Monday); otherwise omit it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "summary": {
                    "type": "string",
                    "description": "Two or three sentences for the user: why this plan.",
                },
                "target_calories": {"type": "integer"},
                "target_protein_grams": {"type": "number"},
                "target_carbohydrate_grams": {"type": "number"},
                "target_fat_grams": {"type": "number"},
                "is_weekly": {"type": "boolean"},
                "meals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "meal_slot": {"type": "string", "description": "From list_meal_slots."},
                            "weekday": {"type": "integer"},
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "food_id": {"type": "integer"},
                                        "recipe_id": {"type": "integer"},
                                        "new_food": _NEW_FOOD_SCHEMA,
                                        "quantity": {"type": "number"},
                                    },
                                    "required": ["quantity"],
                                },
                            },
                        },
                        "required": ["meal_slot", "items"],
                    },
                },
            },
            "required": [
                "name",
                "summary",
                "target_calories",
                "target_protein_grams",
                "target_carbohydrate_grams",
                "target_fat_grams",
                "meals",
            ],
        },
        "handler": propose_diet_plan,
        "activity": _("Putting a diet plan together…"),
    },
    {
        "name": "propose_program",
        "description": (
            "Show the user a workout program they can create with one click. Nothing is "
            "saved unless they accept. Exercises must come from search_exercises. "
            "scheduled_weekday is 0-6 (0 = Monday) or omitted for a rotation. Weights in kg."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "summary": {
                    "type": "string",
                    "description": "Two or three sentences for the user: why this program.",
                },
                "workouts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "scheduled_weekday": {"type": "integer"},
                            "notes": {"type": "string"},
                            "prescriptions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "exercise_id": {"type": "integer"},
                                        "set_count": {"type": "integer"},
                                        "min_reps": {"type": "integer"},
                                        "max_reps": {"type": "integer"},
                                        "target_weight_kg": {"type": "number"},
                                        "target_rpe": {"type": "number"},
                                        "progression_method": {
                                            "type": "string",
                                            "enum": proposals.PROGRESSION_METHODS,
                                        },
                                        "weight_increment_kg": {"type": "number"},
                                        "notes": {"type": "string"},
                                    },
                                    "required": [
                                        "exercise_id",
                                        "set_count",
                                        "min_reps",
                                        "max_reps",
                                    ],
                                },
                            },
                        },
                        "required": ["name", "prescriptions"],
                    },
                },
            },
            "required": ["name", "summary", "workouts"],
        },
        "handler": propose_program,
        "activity": _("Putting a program together…"),
    },
]

_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def definitions():
    """The provider-neutral tool definitions sent to the model - always
    the same list in the same order (a stable, cacheable prompt prefix)."""
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in TOOLS
    ]


def activity_for(name):
    tool = _BY_NAME.get(name)
    return str(tool["activity"]) if tool else ""


def run(ctx, name, args):
    """Runs one tool call. Returns (content, is_error) - never raises for
    anything the model got wrong."""
    tool = _BY_NAME.get(name)
    if tool is None:
        return f"Unknown tool: {name}", True
    if isinstance(args, str):
        # Ollama models sometimes send the arguments as a JSON string.
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return "The tool input wasn't valid JSON.", True
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return "The tool input must be a JSON object.", True
    try:
        return _dumps(tool["handler"](ctx, args)), False
    except ToolInputError as exc:
        return str(exc), True
