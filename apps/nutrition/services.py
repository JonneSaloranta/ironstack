"""Orchestration for the historized goal/target chains — see
docs/NUTRITION.md "NutritionGoal"/"NutritionTarget". Views call these,
never touch the append/supersede logic directly, so there's exactly
one place a goal or target row ever gets closed out.
"""

from django.db import transaction
from django.utils import timezone

from . import energy, macros
from .models import NutritionGoal, NutritionTarget, TargetSource


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
