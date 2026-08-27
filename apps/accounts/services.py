"""Account-level orchestration that reaches across every other app —
lives here rather than in apps.core because deleting or exporting
*this user's own account* is fundamentally an accounts-domain
operation, even though most of the data it touches belongs to other
apps.
"""

import json

from django.core import serializers as django_serializers
from django.db import transaction


@transaction.atomic
def delete_account(user):
    """GDPR Article 17 ("right to erasure") self-service account
    deletion — Profile → "Delete account". Two different things
    happen to this user's data, deliberately not the same treatment
    for everything:

    1. **Exclusively personal data is hard-deleted.** Every model that
       could only ever mean "this specific person's own record" —
       WorkoutSession (and its own PerformedExercise/ExerciseSet
       cascade), PersonalRecord, BodyMeasurement, Activity,
       DiaryEntry, DietPlan (and its own Meal/Item cascade),
       NutritionProfile/Goal/Target, ApiKey, TwoFactorBackupCode,
       Feedback — already has `on_delete=models.CASCADE` on its FK to
       `User` for exactly this reason (see each app's own models.py).
       `user.delete()` below cascades through all of it in one
       transaction; nothing here has to know the individual model
       names to hard-delete them.

    2. **Shared reference content this user happened to create is
       reassigned, not deleted.** A custom Exercise/Food/Recipe/
       Program/MealSlot/ActivityType/MeasurementType has `owner` set
       to this user, but — same "shared-or-own" visibility every one
       of these models already uses (`Q(owner=user) |
       Q(owner__isnull=True)`) — another user on this instance may
       already be actively using it: a workout logged against a
       custom exercise, a diary entry against a custom food. Deleting
       these outright on account deletion would either raise
       `ProtectedError` and block the whole deletion (Exercise/
       MeasurementType/ActivityType's own usage FKs are deliberately
       `on_delete=PROTECT` for exactly this reason) or, for
       nutrition's own `CASCADE`-configured usage FKs, silently
       destroy another user's own diary/recipe history instead. Both
       outcomes are wrong, so this reassigns `owner` to `None` first —
       the same meaning `owner=None` already has everywhere else in
       this app ("a built-in, shared default", e.g. the seeded
       template recipes, an OpenFoodFacts-imported Food) — which lets
       step 1's CASCADE proceed untouched and leaves anyone still
       using the content with exactly what they had before, just
       without this user's name on it.

    Deliberately **not handled here**: existing backup archives
    (`docs/BACKUP.md`) made before this call still contain this user's
    data until they're rotated out by `BackupSettings.retention_count`
    — a point-in-time snapshot can't retroactively un-contain
    something, and disclosing that plainly (`templates/accounts/
    account_delete.html`) is the honest alternative to pretending this
    call reaches backups it structurally can't.
    """
    from apps.activities.models import ActivityType
    from apps.exercises.models import Exercise
    from apps.measurements.models import MeasurementType
    from apps.nutrition.models import Food, MealSlot, Recipe
    from apps.programs.models import Program

    for model in (ActivityType, Exercise, MeasurementType, Food, MealSlot, Recipe, Program):
        model.objects.filter(owner=user).update(owner=None)

    user.delete()


def _dump(queryset):
    """A queryset -> plain JSON-safe list of `{"model", "pk", "fields"}`
    dicts, Django's own generic model serializer rather than a fresh
    per-model field list to maintain — every field on every model
    below is included automatically, including any added after this
    function was written. Round-trips through JSON once here (instead
    of using the "python" format directly) specifically so `Decimal`/
    `date`/`datetime` values already come out as the same JSON-safe
    strings/numbers `export_account_data`'s own final `json.dumps` of
    the *combined* export needs — no custom encoder to maintain."""
    return json.loads(django_serializers.serialize("json", queryset))


def export_account_data(user):
    """Everything exclusively this user's own, as one JSON-serializable
    dict — GDPR Article 20 ("right to data portability"), Profile ->
    "Download your data". Deliberately the same set of models
    `delete_account` above hard-deletes (see that function's own
    docstring for the full reasoning) plus this user's own authored
    "shared reference content" (a custom exercise/food/recipe/program/
    meal slot they created, even though it isn't *exclusively* theirs
    once someone else starts using it) — this is a read of what
    exists today, not a statement about what deleting the account
    would do to each of these.

    `ApiKey` is the one deliberate exception to "every field, via
    Django's own generic serializer": `key_hash` is a credential, not
    data to read back (the same reason a password hash is never shown
    to its own owner either) — handled by hand below instead, listing
    every field except that one explicitly, rather than trust a
    generic dump to never accidentally include it.
    """
    from apps.activities.models import Activity
    from apps.api.models import ApiKey
    from apps.core.models import Feedback
    from apps.exercises.models import Exercise
    from apps.measurements.models import BodyMeasurement
    from apps.nutrition.models import (
        DiaryEntry,
        DietPlan,
        DietPlanItem,
        DietPlanMeal,
        Food,
        MealSlot,
        NutritionGoal,
        NutritionProfile,
        NutritionTarget,
        Recipe,
    )
    from apps.programs.models import Program
    from apps.records.models import PersonalRecord
    from apps.workouts.models import ExerciseSet, PerformedExercise, WorkoutSession

    return {
        # Hand-listed rather than run through Django's generic
        # serializer like everything else below, for the same reason
        # ApiKey's key_hash is hand-excluded: `User` also carries a
        # `password` hash and a plaintext `totp_secret`, neither of
        # which belongs in a user's own export any more than a
        # credential would. Every other field is included — this is
        # meant to be a complete answer to "what do you have on me",
        # not just the handful of fields shown on the profile page.
        "account": {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "date_joined": user.date_joined.isoformat(),
            "unit_system": user.unit_system,
            "timezone": user.timezone,
            "language": user.language,
            "height_meters": str(user.height) if user.height is not None else None,
            "show_bmi": user.show_bmi,
            "show_achievements": user.show_achievements,
            "show_name_to_others": user.show_name_to_others,
            "show_gravatar": user.show_gravatar,
            "onboarding_completed": user.onboarding_completed,
            "is_sso_user": user.is_sso_user,
            "two_factor_enabled": user.totp_enabled,
        },
        "workout_sessions": _dump(WorkoutSession.objects.filter(user=user)),
        "performed_exercises": _dump(PerformedExercise.objects.filter(session__user=user)),
        "exercise_sets": _dump(ExerciseSet.objects.filter(performed_exercise__session__user=user)),
        "personal_records": _dump(PersonalRecord.objects.filter(user=user)),
        "body_measurements": _dump(BodyMeasurement.objects.filter(user=user)),
        "activities": _dump(Activity.objects.filter(user=user)),
        "nutrition_profile": _dump(NutritionProfile.objects.filter(user=user)),
        "nutrition_goals": _dump(NutritionGoal.objects.filter(user=user)),
        "nutrition_targets": _dump(NutritionTarget.objects.filter(user=user)),
        "diary_entries": _dump(DiaryEntry.objects.filter(user=user)),
        "diet_plans": _dump(DietPlan.objects.filter(user=user)),
        "diet_plan_meals": _dump(DietPlanMeal.objects.filter(diet_plan__user=user)),
        "diet_plan_items": _dump(DietPlanItem.objects.filter(diet_plan_meal__diet_plan__user=user)),
        "custom_exercises": _dump(Exercise.objects.filter(owner=user)),
        "custom_foods": _dump(Food.objects.filter(owner=user)),
        "custom_recipes": _dump(Recipe.objects.filter(owner=user)),
        "custom_meal_slots": _dump(MealSlot.objects.filter(owner=user)),
        "custom_programs": _dump(Program.objects.filter(owner=user)),
        "feedback": _dump(Feedback.objects.filter(user=user)),
        "api_keys": [
            {
                "name": key.name,
                "prefix": key.prefix,
                "tier": key.tier.name,
                "is_active": key.is_active,
                "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
                "created_at": key.created_at.isoformat(),
            }
            for key in ApiKey.objects.filter(user=user).select_related("tier")
        ],
    }
