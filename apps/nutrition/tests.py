from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override as translation_override

from apps.measurements.models import BodyMeasurement, MeasurementType
from apps.nutrition import (
    calculators,
    diet_builder,
    energy,
    macros,
    openfoodfacts,
    services,
    suggestions,
    trends,
)

from .forms import BodyStepForm, LogDietPlanForm, LogRecipeForm
from .models import (
    ActivityJob,
    ActivityLevel,
    BiologicalSex,
    DiaryEntry,
    DietPlan,
    DietPlanItem,
    DietPlanMeal,
    Food,
    GoalType,
    MealSlot,
    NutritionGoal,
    NutritionProfile,
    NutritionTarget,
    OpenFoodFactsSettings,
    Recipe,
    RecipeIngredient,
    ServingUnit,
    TargetSource,
)

User = get_user_model()


def make_food(owner, **kwargs):
    defaults = {
        "name": "Chicken breast",
        "serving_size": Decimal("100"),
        "serving_unit": ServingUnit.GRAM,
        "calories": 165,
        "protein_grams": Decimal("31"),
        "carbohydrate_grams": Decimal("0"),
        "fat_grams": Decimal("3.6"),
    }
    defaults.update(kwargs)
    return Food.objects.create(owner=owner, **defaults)


class MealSlotSeedTests(TestCase):
    def test_seed_migration_creates_the_documented_default_slots(self):
        names = list(
            MealSlot.objects.filter(owner=None).order_by("order").values_list("name", flat=True)
        )
        self.assertEqual(names, ["Breakfast", "Lunch", "Dinner", "Evening snack"])

    def test_a_user_can_create_their_own_meal_slot_with_the_same_name_as_another_users(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        MealSlot.objects.create(name="Pre-workout", owner=alice)
        # Should not raise — unique_user_meal_slot_name is scoped per owner.
        MealSlot.objects.create(name="Pre-workout", owner=bob)
        self.assertTrue(MealSlot.objects.filter(name="Pre-workout", owner=alice).exists())
        self.assertTrue(MealSlot.objects.filter(name="Pre-workout", owner=bob).exists())

    def test_a_second_system_meal_slot_with_the_same_name_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MealSlot.objects.create(name="Breakfast", owner=None)

    def test_is_custom_reflects_ownership(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        custom = MealSlot.objects.create(name="Pre-workout", owner=alice)
        system = MealSlot.objects.get(name="Breakfast", owner=None)
        self.assertTrue(custom.is_custom)
        self.assertFalse(system.is_custom)


class NutritionGoalHistoryTests(TestCase):
    """NutritionGoal is append-only — see docs/NUTRITION.md
    "NutritionGoal": setting a new goal must never overwrite an old
    one, only stamp ended_at on it."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_only_one_open_goal_per_user_is_allowed_at_the_database_level(self):
        NutritionGoal.objects.create(
            user=self.alice,
            goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            NutritionGoal.objects.create(
                user=self.alice,
                goal_type=GoalType.MAINTENANCE,
                target_rate_kg_per_week=Decimal("0"),
            )

    def test_ending_a_goal_and_starting_a_new_one_preserves_the_old_row(self):
        first = NutritionGoal.objects.create(
            user=self.alice,
            goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        first.ended_at = first.started_at
        first.save(update_fields=["ended_at"])
        NutritionGoal.objects.create(
            user=self.alice,
            goal_type=GoalType.MAINTENANCE,
            target_rate_kg_per_week=Decimal("0"),
        )
        self.assertEqual(NutritionGoal.objects.filter(user=self.alice).count(), 2)
        self.assertIsNotNone(NutritionGoal.objects.get(pk=first.pk).ended_at)


class NutritionTargetHistoryTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_only_one_open_target_per_user_is_allowed_at_the_database_level(self):
        NutritionTarget.objects.create(
            user=self.alice,
            daily_calories=2500,
            protein_grams=Decimal("180"),
            carbohydrate_grams=Decimal("280"),
            fat_grams=Decimal("70"),
            source=TargetSource.CALCULATED,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            NutritionTarget.objects.create(
                user=self.alice,
                daily_calories=2350,
                protein_grams=Decimal("180"),
                carbohydrate_grams=Decimal("250"),
                fat_grams=Decimal("65"),
                source=TargetSource.ADJUSTED,
            )


class FoodModelTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_str_includes_brand_when_present(self):
        food = make_food(self.alice, brand="Acme")
        self.assertEqual(str(food), "Chicken breast (Acme)")

    def test_str_omits_brand_when_blank(self):
        food = make_food(self.alice)
        self.assertEqual(str(food), "Chicken breast")

    def test_optional_extras_default_to_none_not_zero(self):
        food = make_food(self.alice)
        self.assertIsNone(food.fiber_grams)
        self.assertIsNone(food.sodium_mg)


class RecipeIngredientTests(TestCase):
    def test_ingredients_are_ordered(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        recipe = Recipe.objects.create(owner=alice, name="Chicken Rice Bowl", servings=2)
        chicken = make_food(alice, name="Chicken")
        rice = make_food(alice, name="Rice")
        RecipeIngredient.objects.create(
            recipe=recipe, food=rice, quantity=Decimal("200"), order=1
        )
        RecipeIngredient.objects.create(
            recipe=recipe, food=chicken, quantity=Decimal("300"), order=0
        )
        self.assertEqual(
            list(recipe.ingredients.values_list("food__name", flat=True)), ["Chicken", "Rice"]
        )


class DiaryEntryConstraintTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.food = make_food(self.alice)
        self.recipe = Recipe.objects.create(owner=self.alice, name="Bowl")
        self.meal_slot = MealSlot.objects.get(name="Breakfast", owner=None)

    def test_a_food_entry_is_valid(self):
        entry = DiaryEntry(
            user=self.alice,
            date=date(2026, 1, 1),
            meal_slot=self.meal_slot,
            food=self.food,
            quantity=Decimal("150"),
        )
        entry.full_clean()  # should not raise

    def test_neither_food_nor_recipe_is_rejected_by_clean(self):
        entry = DiaryEntry(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.meal_slot,
            quantity=Decimal("1"),
        )
        with self.assertRaises(ValidationError):
            entry.clean()

    def test_both_food_and_recipe_is_rejected_by_clean(self):
        entry = DiaryEntry(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.meal_slot,
            food=self.food, recipe=self.recipe, quantity=Decimal("1"),
        )
        with self.assertRaises(ValidationError):
            entry.clean()

    def test_neither_food_nor_recipe_is_rejected_at_the_database_level(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            DiaryEntry.objects.create(
                user=self.alice, date=date(2026, 1, 1), meal_slot=self.meal_slot,
                quantity=Decimal("1"),
            )

    def test_both_food_and_recipe_is_rejected_at_the_database_level(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            DiaryEntry.objects.create(
                user=self.alice, date=date(2026, 1, 1), meal_slot=self.meal_slot,
                food=self.food, recipe=self.recipe, quantity=Decimal("1"),
            )


class DietPlanTests(TestCase):
    def test_a_plan_can_have_meals_with_a_calorie_split(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        plan = DietPlan.objects.create(
            user=alice,
            name="Cut plan",
            target_calories=2500,
            target_protein_grams=Decimal("180"),
            target_carbohydrate_grams=Decimal("280"),
            target_fat_grams=Decimal("70"),
        )
        breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        DietPlanMeal.objects.create(diet_plan=plan, meal_slot=breakfast, target_calories=650)
        self.assertEqual(plan.meals.count(), 1)
        self.assertEqual(plan.meals.first().target_calories, 650)


class NutritionProfileModelTests(TestCase):
    def test_one_profile_per_user(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        NutritionProfile.objects.create(
            user=alice,
            biological_sex=BiologicalSex.FEMALE,
            birth_date=date(1995, 1, 1),
            activity_job=ActivityJob.SEDENTARY,
            activity_level=ActivityLevel.MODERATE,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            NutritionProfile.objects.create(
                user=alice,
                biological_sex=BiologicalSex.FEMALE,
                birth_date=date(1995, 1, 1),
                activity_job=ActivityJob.SEDENTARY,
                activity_level=ActivityLevel.MODERATE,
            )


class BMRCalculationTests(TestCase):
    def test_known_value_male(self):
        # Textbook example: 30yo male, 80kg, 180cm.
        bmr = energy.calculate_bmr(Decimal("80"), Decimal("180"), 30, BiologicalSex.MALE)
        self.assertEqual(bmr, Decimal("1780"))

    def test_known_value_female(self):
        # 25yo female, 60kg, 165cm.
        bmr = energy.calculate_bmr(Decimal("60"), Decimal("165"), 25, BiologicalSex.FEMALE)
        self.assertEqual(bmr, Decimal("1345"))

    def test_male_and_female_constants_differ_by_166(self):
        """The Mifflin-St Jeor formula differs only by its final
        constant (+5 male, -161 female) -- same inputs should differ
        by exactly 166."""
        male = energy.calculate_bmr(Decimal("70"), Decimal("170"), 30, BiologicalSex.MALE)
        female = energy.calculate_bmr(Decimal("70"), Decimal("170"), 30, BiologicalSex.FEMALE)
        self.assertEqual(male - female, Decimal("166"))


class TDEECalculationTests(TestCase):
    def test_known_value(self):
        tdee = energy.calculate_tdee(Decimal("1780"), ActivityLevel.MODERATE)
        self.assertEqual(tdee, Decimal("2759"))

    def test_every_activity_level_has_a_multiplier_greater_than_one(self):
        for level, multiplier in energy.ACTIVITY_MULTIPLIERS.items():
            self.assertGreaterEqual(multiplier, Decimal("1"))

    def test_multipliers_increase_monotonically_with_activity(self):
        ordered = [
            energy.ACTIVITY_MULTIPLIERS[level]
            for level in [
                ActivityLevel.SEDENTARY,
                ActivityLevel.LIGHT,
                ActivityLevel.MODERATE,
                ActivityLevel.ACTIVE,
                ActivityLevel.VERY_ACTIVE,
            ]
        ]
        self.assertEqual(ordered, sorted(ordered))


class ActivityLevelSuggestionTests(TestCase):
    def test_no_activity_at_all_suggests_sedentary(self):
        suggestion = energy.suggest_activity_level(activity_job=ActivityJob.SEDENTARY)
        self.assertEqual(suggestion.activity_level, ActivityLevel.SEDENTARY)

    def test_maximum_activity_across_every_signal_suggests_very_active(self):
        suggestion = energy.suggest_activity_level(
            activity_job=ActivityJob.PHYSICAL,
            daily_steps=15000,
            training_sessions_per_week=7,
            other_exercise_minutes_per_week=400,
        )
        self.assertEqual(suggestion.activity_level, ActivityLevel.VERY_ACTIVE)

    def test_a_middling_profile_suggests_moderate(self):
        suggestion = energy.suggest_activity_level(
            activity_job=ActivityJob.MODERATE,
            daily_steps=8000,
            training_sessions_per_week=4,
        )
        self.assertEqual(suggestion.activity_level, ActivityLevel.MODERATE)

    def test_reason_is_a_non_empty_explanation(self):
        suggestion = energy.suggest_activity_level(
            activity_job=ActivityJob.LIGHT, daily_steps=6000
        )
        self.assertTrue(suggestion.reason)
        self.assertIn("Suggested", suggestion.reason)


class RateSafetyTests(TestCase):
    def test_fat_loss_cap_is_one_percent_of_bodyweight(self):
        cap = energy.max_safe_rate_kg_per_week(Decimal("80"), GoalType.FAT_LOSS_AGGRESSIVE)
        self.assertEqual(cap, Decimal("-0.80"))

    def test_muscle_gain_cap_is_half_a_percent_of_bodyweight(self):
        cap = energy.max_safe_rate_kg_per_week(Decimal("80"), GoalType.MUSCLE_GAIN_AGGRESSIVE)
        self.assertEqual(cap, Decimal("0.40"))

    def test_maintenance_cap_is_zero(self):
        cap = energy.max_safe_rate_kg_per_week(Decimal("80"), GoalType.MAINTENANCE)
        self.assertEqual(cap, Decimal("0"))

    def test_a_rate_within_the_cap_is_not_clamped(self):
        clamped = energy.clamp_rate(Decimal("80"), GoalType.FAT_LOSS_MODERATE, Decimal("-0.5"))
        self.assertEqual(clamped, Decimal("-0.5"))

    def test_a_rate_beyond_the_cap_is_clamped_to_it(self):
        clamped = energy.clamp_rate(Decimal("50"), GoalType.FAT_LOSS_AGGRESSIVE, Decimal("-0.75"))
        self.assertEqual(clamped, Decimal("-0.50"))

    def test_muscle_gain_rate_beyond_the_cap_is_clamped_to_it(self):
        clamped = energy.clamp_rate(
            Decimal("50"), GoalType.MUSCLE_GAIN_AGGRESSIVE, Decimal("1.0")
        )
        self.assertEqual(clamped, Decimal("0.25"))


class CalorieFloorTests(TestCase):
    def test_floor_is_the_sex_based_minimum_when_bmr_is_low(self):
        # Small person -> 90% of BMR is below the clinical minimum.
        floor = energy.calorie_floor(Decimal("50"), Decimal("155"), 25, BiologicalSex.FEMALE)
        self.assertEqual(floor, Decimal("1200"))

    def test_floor_rises_above_the_sex_based_minimum_for_a_high_bmr(self):
        # Large/muscular person -> 90% of BMR exceeds the generic 1500.
        floor = energy.calorie_floor(Decimal("130"), Decimal("195"), 25, BiologicalSex.MALE)
        self.assertGreater(floor, Decimal("1500"))


class CalorieTargetTests(TestCase):
    """apps.nutrition.energy.calculate_calorie_target — the full
    goal -> calorie pipeline. See docs/NUTRITION.md "Safety bounds":
    both the rate cap and the absolute floor can fire independently."""

    def test_moderate_cut_matches_the_expected_deficit(self):
        result = energy.calculate_calorie_target(
            tdee=Decimal("2650"),
            weight_kg=Decimal("80"),
            height_cm=Decimal("180"),
            age_years=30,
            biological_sex=BiologicalSex.MALE,
            goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        self.assertEqual(result.daily_calories, 2100)
        self.assertFalse(result.rate_was_capped)
        self.assertFalse(result.floor_was_applied)

    def test_maintenance_returns_tdee_unchanged(self):
        result = energy.calculate_calorie_target(
            tdee=Decimal("2650"),
            weight_kg=Decimal("80"),
            height_cm=Decimal("180"),
            age_years=30,
            biological_sex=BiologicalSex.MALE,
            goal_type=GoalType.MAINTENANCE,
            target_rate_kg_per_week=Decimal("0"),
        )
        self.assertEqual(result.daily_calories, 2650)

    def test_an_unsafe_rate_and_a_resulting_floor_breach_are_both_reported(self):
        # Small person requesting an aggressive cut: the rate itself
        # gets capped, and the resulting calories still need the
        # absolute floor.
        result = energy.calculate_calorie_target(
            tdee=Decimal("1419"),
            weight_kg=Decimal("50"),
            height_cm=Decimal("155"),
            age_years=25,
            biological_sex=BiologicalSex.FEMALE,
            goal_type=GoalType.FAT_LOSS_AGGRESSIVE,
            target_rate_kg_per_week=Decimal("-0.75"),
        )
        self.assertTrue(result.rate_was_capped)
        self.assertTrue(result.floor_was_applied)
        self.assertEqual(result.daily_calories, 1200)

    def test_reason_is_never_empty_and_mentions_the_tdee(self):
        result = energy.calculate_calorie_target(
            tdee=Decimal("2650"),
            weight_kg=Decimal("80"),
            height_cm=Decimal("180"),
            age_years=30,
            biological_sex=BiologicalSex.MALE,
            goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        self.assertIn("2650", result.reason)

    def test_muscle_gain_increases_calories_above_tdee(self):
        result = energy.calculate_calorie_target(
            tdee=Decimal("2650"),
            weight_kg=Decimal("80"),
            height_cm=Decimal("180"),
            age_years=30,
            biological_sex=BiologicalSex.MALE,
            goal_type=GoalType.MUSCLE_GAIN_LEAN,
            target_rate_kg_per_week=Decimal("0.125"),
        )
        self.assertGreater(result.daily_calories, 2650)


class MacroCalculationTests(TestCase):
    def test_maintenance_known_values(self):
        result = macros.calculate_macros(Decimal("80"), 2500, GoalType.MAINTENANCE)
        self.assertEqual(result.protein_grams, Decimal("144.00"))
        self.assertEqual(result.fat_grams, Decimal("69.44"))
        self.assertEqual(result.carbohydrate_grams, Decimal("324.75"))
        self.assertFalse(result.fat_was_reduced)
        self.assertFalse(result.protein_was_reduced)

    def test_fat_loss_uses_higher_protein_per_kg(self):
        result = macros.calculate_macros(Decimal("80"), 2100, GoalType.FAT_LOSS_MODERATE)
        self.assertEqual(result.protein_grams, Decimal("176.00"))
        self.assertEqual(result.fat_grams, Decimal("58.33"))
        self.assertEqual(result.carbohydrate_grams, Decimal("217.75"))

    def test_grams_and_kcal_always_agree(self):
        """Regression guard: kcal figures are derived from the
        *quantized* grams, not the other way around, so a displayed
        gram figure and its kcal figure can never silently disagree."""
        result = macros.calculate_macros(Decimal("80"), 2500, GoalType.MAINTENANCE)
        self.assertEqual(result.protein_kcal, result.protein_grams * 4)
        self.assertEqual(result.fat_kcal, result.fat_grams * 9)
        self.assertEqual(result.carbohydrate_kcal, result.carbohydrate_grams * 4)

    def test_percentages_sum_to_roughly_a_hundred(self):
        result = macros.calculate_macros(Decimal("80"), 2500, GoalType.MAINTENANCE)
        total = result.protein_percent + result.carbohydrate_percent + result.fat_percent
        self.assertAlmostEqual(float(total), 100.0, delta=0.2)

    def test_a_very_low_calorie_high_protein_target_reduces_fat_before_going_negative(self):
        result = macros.calculate_macros(Decimal("100"), 1000, GoalType.FAT_LOSS_MODERATE)
        self.assertTrue(result.fat_was_reduced)
        self.assertFalse(result.protein_was_reduced)
        self.assertEqual(result.carbohydrate_grams, Decimal("0.00"))
        self.assertGreaterEqual(result.fat_grams, Decimal("0"))

    def test_an_extreme_target_where_protein_alone_exceeds_calories_reduces_protein_too(self):
        result = macros.calculate_macros(Decimal("150"), 1000, GoalType.FAT_LOSS_MODERATE)
        self.assertTrue(result.protein_was_reduced)
        self.assertTrue(result.fat_was_reduced)
        self.assertEqual(result.protein_kcal, Decimal("1000"))
        self.assertEqual(result.fat_grams, Decimal("0.00"))
        self.assertEqual(result.carbohydrate_grams, Decimal("0.00"))

    def test_custom_protein_and_fat_overrides_are_respected(self):
        result = macros.calculate_macros(
            Decimal("80"),
            2500,
            GoalType.MAINTENANCE,
            protein_g_per_kg=Decimal("3.0"),
            fat_percent=Decimal("0.3"),
        )
        self.assertEqual(result.protein_grams, Decimal("240.00"))
        # fat_kcal is derived from the quantized grams (83.33 g), so it
        # lands a hair under the raw 750 kcal target -- by design, see
        # calculate_macros' own docstring.
        self.assertAlmostEqual(float(result.fat_kcal), 750.0, delta=0.1)

    def test_zero_calories_does_not_divide_by_zero(self):
        result = macros.calculate_macros(Decimal("80"), 0, GoalType.MAINTENANCE)
        self.assertEqual(result.protein_percent, Decimal("0"))
        self.assertEqual(result.carbohydrate_percent, Decimal("0"))
        self.assertEqual(result.fat_percent, Decimal("0"))


def _d(day_offset):
    return date(2026, 1, 1) + timedelta(days=day_offset)


class BucketByDayTests(TestCase):
    def test_same_day_readings_are_averaged(self):
        readings = [(_d(0), Decimal("80.0")), (_d(0), Decimal("80.4"))]
        self.assertEqual(trends.bucket_by_day(readings), {_d(0): Decimal("80.2")})

    def test_different_days_stay_separate(self):
        readings = [(_d(0), Decimal("80.0")), (_d(1), Decimal("79.5"))]
        self.assertEqual(
            trends.bucket_by_day(readings), {_d(0): Decimal("80.0"), _d(1): Decimal("79.5")}
        )


class MovingAverageTrendTests(TestCase):
    def test_the_window_grows_until_it_reaches_full_size(self):
        """Early points, with no earlier history yet, average over
        whatever's actually available rather than treating missing
        days as zero."""
        readings = [(_d(0), Decimal("80")), (_d(1), Decimal("79")), (_d(2), Decimal("78"))]
        trend = trends.moving_average_trend(readings)
        self.assertEqual(
            trend, [(_d(0), Decimal("80")), (_d(1), Decimal("79.5")), (_d(2), Decimal("79"))]
        )

    def test_empty_readings_returns_an_empty_trend(self):
        self.assertEqual(trends.moving_average_trend([]), [])


class ComputeTrendTests(TestCase):
    def test_fewer_than_the_minimum_distinct_days_returns_none(self):
        readings = [(_d(0), Decimal("80")), (_d(20), Decimal("78")), (_d(40), Decimal("76"))]
        self.assertIsNone(trends.compute_trend(readings))

    def test_a_span_shorter_than_the_minimum_returns_none_even_with_enough_days(self):
        readings = [
            (_d(0), Decimal("80")), (_d(3), Decimal("79.5")),
            (_d(6), Decimal("79")), (_d(9), Decimal("78.5")),
        ]
        self.assertIsNone(trends.compute_trend(readings))

    def test_a_constant_weight_yields_a_zero_rate(self):
        readings = [(_d(offset), Decimal("80.0")) for offset in (0, 5, 10, 15, 20)]
        result = trends.compute_trend(readings)
        self.assertIsNotNone(result)
        self.assertEqual(result.actual_rate_kg_per_week, Decimal("0.000"))

    def test_isolated_widely_spaced_readings_give_an_exact_known_rate(self):
        # Each reading is >7 days from its neighbors, so the moving
        # average never smooths across readings -- the rate is exactly
        # (77.0 - 80.0) kg over 30/7 weeks.
        readings = [
            (_d(0), Decimal("80.0")), (_d(10), Decimal("79.0")),
            (_d(20), Decimal("78.0")), (_d(30), Decimal("77.0")),
        ]
        result = trends.compute_trend(readings)
        self.assertEqual(result.actual_rate_kg_per_week, Decimal("-0.700"))
        self.assertEqual(result.span_days, 30)
        self.assertEqual(result.distinct_days, 4)

    def test_an_increasing_trend_yields_a_positive_rate(self):
        readings = [
            (_d(0), Decimal("70.0")), (_d(10), Decimal("70.5")),
            (_d(20), Decimal("71.0")), (_d(30), Decimal("71.5")),
        ]
        result = trends.compute_trend(readings)
        self.assertGreater(result.actual_rate_kg_per_week, Decimal("0"))


def _log_weight(user, day_offset, weight_kg):
    from datetime import datetime
    from datetime import timezone as dt_timezone

    body_weight_type = MeasurementType.objects.get(name="Body weight", owner=None)
    return BodyMeasurement.objects.create(
        user=user,
        measurement_type=body_weight_type,
        value=Decimal(str(weight_kg)),
        recorded_at=datetime.combine(_d(day_offset), datetime.min.time(), tzinfo=dt_timezone.utc),
    )


class CalorieAdjustmentSuggestionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def _set_goal_and_target(self, rate, calories=2100):
        NutritionGoal.objects.create(
            user=self.alice, goal_type=GoalType.FAT_LOSS_MODERATE, target_rate_kg_per_week=rate
        )
        NutritionTarget.objects.create(
            user=self.alice,
            daily_calories=calories,
            protein_grams=Decimal("176"),
            carbohydrate_grams=Decimal("218"),
            fat_grams=Decimal("58"),
            source="calculated",
        )

    def test_no_goal_returns_no_active_goal(self):
        result = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertEqual(result.action, suggestions.AdjustmentAction.NO_ACTIVE_GOAL)

    def test_goal_without_a_target_returns_no_active_goal(self):
        NutritionGoal.objects.create(
            user=self.alice,
            goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        result = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertEqual(result.action, suggestions.AdjustmentAction.NO_ACTIVE_GOAL)

    def test_no_weight_history_returns_insufficient_data(self):
        self._set_goal_and_target(Decimal("-0.5"))
        result = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertEqual(result.action, suggestions.AdjustmentAction.INSUFFICIENT_DATA)

    def test_a_trend_right_at_the_tolerance_boundary_is_on_track(self):
        self._set_goal_and_target(Decimal("-0.5"))
        for offset, weight in [(0, "80.0"), (10, "79.5"), (20, "79.0"), (30, "78.5")]:
            _log_weight(self.alice, offset, weight)
        result = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertEqual(result.actual_rate_kg_per_week, Decimal("-0.350"))
        self.assertEqual(result.action, suggestions.AdjustmentAction.ON_TRACK)
        self.assertIsNone(result.suggested_daily_calories)

    def test_losing_too_slowly_suggests_a_calorie_decrease(self):
        self._set_goal_and_target(Decimal("-0.5"), calories=2100)
        for offset, weight in [(0, "80.0"), (10, "79.8"), (20, "79.6"), (30, "79.4")]:
            _log_weight(self.alice, offset, weight)
        result = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertEqual(result.action, suggestions.AdjustmentAction.ADJUST)
        self.assertEqual(result.actual_rate_kg_per_week, Decimal("-0.140"))
        self.assertLess(result.delta_kcal, 0)
        self.assertEqual(result.suggested_daily_calories, 2100 + result.delta_kcal)

    def test_gaining_too_fast_on_a_cut_suggests_a_calorie_decrease_too(self):
        # Losing *faster* than a fat-loss target is still "off track,"
        # in the direction of needing more calories, not fewer.
        self._set_goal_and_target(Decimal("-0.5"), calories=2100)
        for offset, weight in [(0, "80.0"), (10, "79.0"), (20, "78.0"), (30, "77.0")]:
            _log_weight(self.alice, offset, weight)
        result = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertEqual(result.action, suggestions.AdjustmentAction.ADJUST)
        self.assertGreater(result.delta_kcal, 0)

    def test_a_single_adjustment_is_capped_even_if_the_raw_gap_is_larger(self):
        self._set_goal_and_target(Decimal("-0.5"), calories=2100)
        for offset, weight in [(0, "80.0"), (10, "79.8"), (20, "79.6"), (30, "79.4")]:
            _log_weight(self.alice, offset, weight)
        result = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertLessEqual(abs(result.delta_kcal), suggestions.MAX_SINGLE_ADJUSTMENT_KCAL)

    def test_low_confidence_with_the_bare_minimum_of_data(self):
        self._set_goal_and_target(Decimal("-0.5"))
        for offset, weight in [(0, "80.0"), (10, "79.8"), (20, "79.6"), (30, "79.4")]:
            _log_weight(self.alice, offset, weight)
        result = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertEqual(result.confidence, suggestions.Confidence.LOW)

    def test_high_confidence_with_a_long_dense_history(self):
        self._set_goal_and_target(Decimal("-0.5"))
        for offset in range(25):
            _log_weight(self.alice, offset, Decimal("80.0") - Decimal("0.03") * offset)
        result = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertEqual(result.confidence, suggestions.Confidence.HIGH)

    def test_result_is_deterministic_for_the_same_data(self):
        self._set_goal_and_target(Decimal("-0.5"))
        for offset, weight in [(0, "80.0"), (10, "79.8"), (20, "79.6"), (30, "79.4")]:
            _log_weight(self.alice, offset, weight)
        first = suggestions.suggest_calorie_adjustment(self.alice)
        second = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertEqual(first, second)

    def test_reason_is_never_empty(self):
        result = suggestions.suggest_calorie_adjustment(self.alice)
        self.assertTrue(result.reason)


class GoalAndTargetServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_set_goal_ends_the_previous_open_goal_and_creates_a_new_one(self):
        first = services.set_goal(
            self.alice, goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        second = services.set_goal(
            self.alice, goal_type=GoalType.MAINTENANCE, target_rate_kg_per_week=Decimal("0")
        )
        first.refresh_from_db()
        self.assertIsNotNone(first.ended_at)
        self.assertIsNone(second.ended_at)
        self.assertEqual(NutritionGoal.objects.filter(user=self.alice).count(), 2)

    def test_set_target_ends_the_previous_open_target_and_creates_a_new_one(self):
        breakdown = macros.calculate_macros(Decimal("80"), 2500, GoalType.MAINTENANCE)
        first = services.set_target(
            self.alice, goal=None, daily_calories=2500, macro_breakdown=breakdown,
            source=TargetSource.CALCULATED, reason="initial",
        )
        second = services.set_target(
            self.alice, goal=None, daily_calories=2350, macro_breakdown=breakdown,
            source=TargetSource.ADJUSTED, reason="adjusted",
        )
        first.refresh_from_db()
        self.assertIsNotNone(first.ended_at)
        self.assertIsNone(second.ended_at)
        self.assertEqual(NutritionTarget.objects.filter(user=self.alice).count(), 2)


class CalculateTargetForGoalTests(TestCase):
    def test_matches_calling_energy_and_macros_directly(self):
        today = date.today()
        birth_date = date(today.year - 30, today.month, today.day)
        profile = NutritionProfile(
            biological_sex=BiologicalSex.MALE,
            birth_date=birth_date,
            activity_job=ActivityJob.SEDENTARY,
            activity_level=ActivityLevel.MODERATE,
        )
        calorie_result, macro_result = services.calculate_target_for_goal(
            profile,
            weight_kg=Decimal("80"),
            height_cm=Decimal("180"),
            goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        expected_bmr = energy.calculate_bmr(Decimal("80"), Decimal("180"), 30, BiologicalSex.MALE)
        expected_tdee = energy.calculate_tdee(expected_bmr, ActivityLevel.MODERATE)
        expected_calorie_result = energy.calculate_calorie_target(
            tdee=expected_tdee,
            weight_kg=Decimal("80"),
            height_cm=Decimal("180"),
            age_years=30,
            biological_sex=BiologicalSex.MALE,
            goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        self.assertEqual(calorie_result.daily_calories, expected_calorie_result.daily_calories)
        expected_macros = macros.calculate_macros(
            Decimal("80"), calorie_result.daily_calories, GoalType.FAT_LOSS_MODERATE
        )
        self.assertEqual(macro_result.protein_grams, expected_macros.protein_grams)
        self.assertIn(str(int(expected_tdee)), calorie_result.reason)


class ApplyAdjustmentSuggestionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.goal = services.set_goal(
            self.alice, goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        breakdown = macros.calculate_macros(Decimal("80"), 2100, GoalType.FAT_LOSS_MODERATE)
        self.target = services.set_target(
            self.alice, goal=self.goal, daily_calories=2100, macro_breakdown=breakdown,
            source=TargetSource.CALCULATED, reason="initial",
        )
        _log_weight(self.alice, 0, "80.0")

    def test_accepting_a_suggestion_creates_a_new_adjusted_target(self):
        suggestion = suggestions.AdjustmentSuggestion(
            action=suggestions.AdjustmentAction.ADJUST,
            target_rate_kg_per_week=Decimal("-0.5"),
            actual_rate_kg_per_week=Decimal("-0.1"),
            suggested_daily_calories=1900,
            delta_kcal=-200,
            confidence=suggestions.Confidence.MEDIUM,
            reason="Test reason",
        )
        new_target = services.apply_adjustment_suggestion(self.alice, suggestion)
        self.target.refresh_from_db()
        self.assertIsNotNone(self.target.ended_at)
        self.assertEqual(new_target.daily_calories, 1900)
        self.assertEqual(new_target.source, TargetSource.ADJUSTED)
        self.assertEqual(new_target.reason, "Test reason")
        self.assertEqual(new_target.goal, self.goal)


RAW_OFF_PRODUCT = {
    "code": "1234567890123",
    "product_name": "Test Muesli",
    "brands": "Acme, Other Brand",
    "nutriscore_grade": "c",
    "nova_group": 3,
    "nutriments": {
        "energy-kcal_100g": 350,
        "proteins_100g": 10.5,
        "carbohydrates_100g": 60,
        "fat_100g": 8,
        "fiber_100g": 7,
        "sugars_100g": 15,
        "saturated-fat_100g": 1.5,
        "sodium_100g": 0.2,
    },
}


class ParseProductTests(TestCase):
    def test_a_complete_product_parses_correctly(self):
        parsed = openfoodfacts.parse_product(RAW_OFF_PRODUCT)
        self.assertEqual(parsed["off_id"], "1234567890123")
        self.assertEqual(parsed["name"], "Test Muesli")
        self.assertEqual(parsed["brand"], "Acme")
        self.assertEqual(parsed["calories"], 350)
        self.assertEqual(parsed["protein_grams"], Decimal("10.5"))
        self.assertEqual(parsed["sodium_mg"], 200)
        self.assertEqual(parsed["nutri_score"], "c")
        self.assertEqual(parsed["nova_group"], 3)

    def test_an_ungraded_products_score_and_nova_group_are_none_not_a_guess(self):
        raw = {**RAW_OFF_PRODUCT, "nutriscore_grade": "unknown", "nova_group": None}
        parsed = openfoodfacts.parse_product(raw)
        self.assertIsNone(parsed["nutri_score"])
        self.assertIsNone(parsed["nova_group"])

    def test_missing_barcode_returns_none(self):
        raw = {**RAW_OFF_PRODUCT, "code": None}
        self.assertIsNone(openfoodfacts.parse_product(raw))

    def test_missing_core_macro_returns_none(self):
        raw = {**RAW_OFF_PRODUCT, "nutriments": {**RAW_OFF_PRODUCT["nutriments"]}}
        del raw["nutriments"]["fat_100g"]
        self.assertIsNone(openfoodfacts.parse_product(raw))

    def test_missing_optional_extras_are_none_not_zero(self):
        raw = {
            "code": "111",
            "product_name": "Bare product",
            "nutriments": {
                "energy-kcal_100g": 100,
                "proteins_100g": 1,
                "carbohydrates_100g": 1,
                "fat_100g": 1,
            },
        }
        parsed = openfoodfacts.parse_product(raw)
        self.assertIsNone(parsed["fiber_grams"])
        self.assertIsNone(parsed["sodium_mg"])


class ImportOrRefreshFoodFromOffTests(TestCase):
    def setUp(self):
        OpenFoodFactsSettings.objects.all().delete()

    def test_creates_a_new_shared_food_on_first_import(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT):
            food = services.import_or_refresh_food_from_off("1234567890123")
        self.assertIsNotNone(food)
        self.assertIsNone(food.owner)
        self.assertEqual(food.off_id, "1234567890123")
        self.assertIsNotNone(food.off_synced_at)

    def test_a_fresh_existing_food_is_returned_without_a_network_call(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT):
            services.import_or_refresh_food_from_off("1234567890123")
        with mock.patch.object(openfoodfacts, "get_product") as mocked:
            services.import_or_refresh_food_from_off("1234567890123")
        mocked.assert_not_called()

    def test_force_refreshes_a_fresh_existing_food_anyway(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT):
            food = services.import_or_refresh_food_from_off("1234567890123")
        first_synced_at = food.off_synced_at
        with mock.patch.object(
            openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT
        ) as mocked:
            refreshed = services.import_or_refresh_food_from_off(
                "1234567890123", force=True
            )
        mocked.assert_called_once_with("1234567890123")
        self.assertGreater(refreshed.off_synced_at, first_synced_at)

    def test_a_stale_existing_food_is_refreshed(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT):
            food = services.import_or_refresh_food_from_off("1234567890123")
        food.off_synced_at = timezone.now() - timedelta(days=20)
        food.calories = 999
        food.save()
        updated_raw = {**RAW_OFF_PRODUCT}
        with mock.patch.object(openfoodfacts, "get_product", return_value=updated_raw):
            refreshed = services.import_or_refresh_food_from_off("1234567890123")
        self.assertEqual(refreshed.pk, food.pk)
        self.assertEqual(refreshed.calories, 350)

    def test_disabled_settings_returns_none_without_a_network_call(self):
        OpenFoodFactsSettings.objects.create(pk=1, enabled=False)
        with mock.patch.object(openfoodfacts, "get_product") as mocked:
            result = services.import_or_refresh_food_from_off("1234567890123")
        self.assertIsNone(result)
        mocked.assert_not_called()

    def test_a_network_error_falls_back_to_the_existing_stale_row(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT):
            food = services.import_or_refresh_food_from_off("1234567890123")
        food.off_synced_at = timezone.now() - timedelta(days=20)
        food.save()
        with mock.patch.object(
            openfoodfacts, "get_product", side_effect=openfoodfacts.OpenFoodFactsError("boom")
        ):
            result = services.import_or_refresh_food_from_off("1234567890123")
        self.assertEqual(result.pk, food.pk)

    def test_no_product_found_returns_none_for_a_brand_new_barcode(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=None):
            result = services.import_or_refresh_food_from_off("0000000000000")
        self.assertIsNone(result)


class MergeFoodsTests(TestCase):
    """apps.nutrition.services.merge_foods — the admin-only duplicate
    cleanup tool. The core guarantee under test: nothing a user
    actually logged (a DiaryEntry, a RecipeIngredient, a
    DietPlanItem) is ever deleted by a merge, only repointed."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.keep = make_food(None, name="Chicken Breast")
        self.duplicate = make_food(None, name="Chicken breast (dupe)")

    def test_diary_entries_are_repointed_not_deleted(self):
        slot = MealSlot.objects.get(name="Lunch", owner=None)
        entry = DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=slot,
            food=self.duplicate, quantity=Decimal("100"),
        )
        services.merge_foods(self.keep, [self.keep, self.duplicate])
        entry.refresh_from_db()
        self.assertEqual(entry.food, self.keep)

    def test_recipe_ingredients_are_repointed_not_deleted(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=1)
        ingredient = RecipeIngredient.objects.create(
            recipe=recipe, food=self.duplicate, quantity=Decimal("100")
        )
        services.merge_foods(self.keep, [self.keep, self.duplicate])
        ingredient.refresh_from_db()
        self.assertEqual(ingredient.food, self.keep)

    def test_diet_plan_items_are_repointed_not_deleted(self):
        plan = DietPlan.objects.create(
            user=self.alice, name="Plan", target_calories=2000,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"),
        )
        slot = MealSlot.objects.get(name="Lunch", owner=None)
        meal = DietPlanMeal.objects.create(diet_plan=plan, meal_slot=slot, target_calories=500)
        item = DietPlanItem.objects.create(
            diet_plan_meal=meal, food=self.duplicate, quantity=Decimal("100")
        )
        services.merge_foods(self.keep, [self.keep, self.duplicate])
        item.refresh_from_db()
        self.assertEqual(item.food, self.keep)

    def test_the_duplicate_is_deleted_and_the_kept_food_is_not(self):
        services.merge_foods(self.keep, [self.keep, self.duplicate])
        self.assertFalse(Food.objects.filter(pk=self.duplicate.pk).exists())
        self.assertTrue(Food.objects.filter(pk=self.keep.pk).exists())

    def test_merging_more_than_two_at_once(self):
        third = make_food(None, name="Chicken breast (another dupe)")
        services.merge_foods(self.keep, [self.keep, self.duplicate, third])
        self.assertFalse(Food.objects.filter(pk=self.duplicate.pk).exists())
        self.assertFalse(Food.objects.filter(pk=third.pk).exists())
        self.assertTrue(Food.objects.filter(pk=self.keep.pk).exists())


class FoodMergeAdminViewTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin", password="s3cret-pass", email="admin@example.com"
        )
        self.client.login(username="admin", password="s3cret-pass")
        self.keep = make_food(None, name="Chicken Breast")
        self.duplicate = make_food(None, name="Chicken breast (dupe)")

    def test_the_action_redirects_to_the_merge_view_with_the_selected_ids(self):
        response = self.client.post(
            reverse("admin:nutrition_food_changelist"),
            {
                "action": "merge_selected_foods",
                "_selected_action": [self.keep.pk, self.duplicate.pk],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("nutrition/food/merge/", response.url)

    def test_the_action_warns_and_does_nothing_with_only_one_selected(self):
        response = self.client.post(
            reverse("admin:nutrition_food_changelist"),
            {"action": "merge_selected_foods", "_selected_action": [self.keep.pk]},
            follow=True,
        )
        self.assertContains(response, "Select at least two foods to merge.")
        self.assertTrue(Food.objects.filter(pk=self.duplicate.pk).exists())

    def test_get_renders_a_form_listing_both_foods(self):
        response = self.client.get(
            reverse("admin:nutrition_food_merge"),
            {"ids": f"{self.keep.pk},{self.duplicate.pk}"},
        )
        self.assertContains(response, "Chicken Breast")
        self.assertContains(response, "Chicken breast (dupe)")

    def test_post_merges_and_redirects_to_the_changelist(self):
        response = self.client.post(
            reverse("admin:nutrition_food_merge"),
            {"ids": f"{self.keep.pk},{self.duplicate.pk}", "keep": self.keep.pk},
        )
        self.assertRedirects(response, reverse("admin:nutrition_food_changelist"))
        self.assertFalse(Food.objects.filter(pk=self.duplicate.pk).exists())

    def test_requires_staff_login(self):
        self.client.logout()
        response = self.client.get(
            reverse("admin:nutrition_food_merge"),
            {"ids": f"{self.keep.pk},{self.duplicate.pk}"},
        )
        self.assertEqual(response.status_code, 302)


class RefreshSelectedFromOffAdminActionTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin", password="s3cret-pass", email="admin@example.com"
        )
        self.client.login(username="admin", password="s3cret-pass")

    def test_refreshes_only_the_off_imported_foods_in_the_selection(self):
        off_food = make_food(None, name="Muesli", off_id="1234567890123")
        custom_food = make_food(None, name="Homemade soup")
        with mock.patch.object(
            openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT
        ) as mocked:
            response = self.client.post(
                reverse("admin:nutrition_food_changelist"),
                {
                    "action": "refresh_selected_from_off",
                    "_selected_action": [off_food.pk, custom_food.pk],
                },
                follow=True,
            )
        mocked.assert_called_once_with("1234567890123")
        self.assertContains(response, "Refreshed 1 of 1 food(s) from OpenFoodFacts.")

    def test_none_selected_have_an_off_id_shows_a_warning_and_makes_no_calls(self):
        custom_food = make_food(None, name="Homemade soup")
        with mock.patch.object(openfoodfacts, "get_product") as mocked:
            response = self.client.post(
                reverse("admin:nutrition_food_changelist"),
                {"action": "refresh_selected_from_off", "_selected_action": [custom_food.pk]},
                follow=True,
            )
        mocked.assert_not_called()
        self.assertContains(
            response, "None of the selected foods were imported from OpenFoodFacts."
        )


class SearchFoodsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_finds_the_users_own_and_shared_local_foods(self):
        make_food(self.alice, name="Alice's Chicken")
        make_food(None, name="Shared Rice")
        make_food(None, name="Unrelated Broccoli")
        with mock.patch.object(openfoodfacts, "search_products", return_value=[]):
            local, off_results = services.search_foods(self.alice, "chicken")
        self.assertEqual([f.name for f in local], ["Alice's Chicken"])

    def test_off_results_already_imported_locally_are_not_repeated(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT):
            services.import_or_refresh_food_from_off("1234567890123")
        with mock.patch.object(
            openfoodfacts, "search_products", return_value=[RAW_OFF_PRODUCT]
        ):
            local, off_results = services.search_foods(self.alice, "muesli")
        self.assertEqual(off_results, [])

    def test_an_off_error_yields_an_empty_off_result_list_not_a_crash(self):
        with mock.patch.object(
            openfoodfacts, "search_products", side_effect=openfoodfacts.OpenFoodFactsError("x")
        ):
            local, off_results = services.search_foods(self.alice, "anything")
        self.assertEqual(off_results, [])

    def test_a_barcode_like_query_uses_the_by_barcode_lookup_not_free_text_search(self):
        with mock.patch.object(
            openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT
        ) as get_product, mock.patch.object(openfoodfacts, "search_products") as search_products:
            local, off_results = services.search_foods(self.alice, "1234567890123")
        get_product.assert_called_once_with("1234567890123")
        search_products.assert_not_called()
        self.assertEqual([r["off_id"] for r in off_results], ["1234567890123"])

    def test_a_barcode_query_also_matches_an_already_imported_local_food_by_off_id(self):
        make_food(self.alice, name="Muesli I already have", off_id="1234567890123")
        with mock.patch.object(openfoodfacts, "get_product", return_value=None):
            local, off_results = services.search_foods(self.alice, "1234567890123")
        self.assertEqual([f.name for f in local], ["Muesli I already have"])

    def test_a_barcode_query_with_no_off_match_returns_no_off_results_not_a_crash(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=None):
            local, off_results = services.search_foods(self.alice, "99999999999999")
        self.assertEqual(off_results, [])

    def test_a_short_digit_string_is_treated_as_a_name_search_not_a_barcode(self):
        # Below the 8-digit floor for any real barcode format — e.g. a
        # quantity typo or a product code fragment, not a barcode.
        with mock.patch.object(
            openfoodfacts, "search_products", return_value=[]
        ) as search_products, mock.patch.object(openfoodfacts, "get_product") as get_product:
            services.search_foods(self.alice, "1234")
        search_products.assert_called_once_with("1234")
        get_product.assert_not_called()


RAW_OFF_CATEGORIES = {
    "tags": [
        {"id": "en:cereals", "name": "Cereals", "products": 5000},
        {"id": "fr:cereales", "name": "Céréales", "products": 4000},
        {"id": "en:snacks", "name": "Snacks", "products": 200},
        {"id": "en:no-name", "products": 100},
    ]
}


class ListCategoriesTests(TestCase):
    def test_ranks_by_product_count_and_skips_non_english_or_unnamed_tags(self):
        with mock.patch("requests.get") as get:
            get.return_value.json.return_value = RAW_OFF_CATEGORIES
            get.return_value.raise_for_status.return_value = None
            categories = openfoodfacts.list_categories(limit=10)
        # fr:cereales (no "en:" id) and en:no-name (no name) are
        # skipped; en:cereals and en:snacks both qualify and are
        # ranked by product count, highest first.
        self.assertEqual(
            categories,
            [
                {"id": "en:cereals", "name": "Cereals", "products": 5000},
                {"id": "en:snacks", "name": "Snacks", "products": 200},
            ],
        )

    def test_respects_the_limit(self):
        with mock.patch("requests.get") as get:
            get.return_value.json.return_value = {
                "tags": [
                    {"id": f"en:cat-{i}", "name": f"Cat {i}", "products": 100 - i}
                    for i in range(10)
                ]
            }
            get.return_value.raise_for_status.return_value = None
            categories = openfoodfacts.list_categories(limit=3)
        self.assertEqual(len(categories), 3)

    def test_a_network_failure_returns_an_empty_list_not_a_crash(self):
        with mock.patch(
            "requests.get", side_effect=openfoodfacts.requests.RequestException("boom")
        ):
            self.assertEqual(openfoodfacts.list_categories(), [])


class SuggestedCategoriesTests(TestCase):
    def test_disabled_integration_returns_an_empty_list(self):
        settings_row = OpenFoodFactsSettings.load()
        settings_row.enabled = False
        settings_row.save()
        self.assertEqual(services.suggested_categories(), [])

    def test_caches_the_result(self):
        with mock.patch.object(
            openfoodfacts, "list_categories", return_value=[{"id": "en:x", "name": "X"}]
        ) as list_categories:
            services.suggested_categories()
            services.suggested_categories()
        list_categories.assert_called_once()


class BrowseCategoryTests(TestCase):
    def test_returns_parsed_products_not_already_imported_locally(self):
        with mock.patch.object(
            openfoodfacts, "search_by_category", return_value=[RAW_OFF_PRODUCT]
        ):
            results = services.browse_category("en:cereals")
        self.assertEqual([r["off_id"] for r in results], ["1234567890123"])

    def test_a_product_already_imported_locally_is_skipped(self):
        Food.objects.create(
            owner=None, name="Already imported", serving_size=Decimal("100"),
            serving_unit=ServingUnit.GRAM, calories=1, protein_grams=Decimal("0"),
            carbohydrate_grams=Decimal("0"), fat_grams=Decimal("0"),
            off_id="1234567890123",
        )
        with mock.patch.object(
            openfoodfacts, "search_by_category", return_value=[RAW_OFF_PRODUCT]
        ):
            results = services.browse_category("en:cereals")
        self.assertEqual(results, [])

    def test_disabled_integration_returns_an_empty_list(self):
        settings_row = OpenFoodFactsSettings.load()
        settings_row.enabled = False
        settings_row.save()
        self.assertEqual(services.browse_category("en:cereals"), [])


class FoodBrowseViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_renders_with_categories(self):
        with mock.patch.object(
            services, "suggested_categories", return_value=[{"id": "en:x", "name": "X"}]
        ):
            response = self.client.get(reverse("nutrition:food-browse"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "X")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:food-browse"))
        self.assertEqual(response.status_code, 302)

    def test_the_camera_barcode_scanner_is_wired_up(self):
        response = self.client.get(reverse("nutrition:food-browse"))
        self.assertContains(response, "barcode-scanner.js")
        self.assertContains(response, "ironstackBarcodeScanner()")


class FoodCategoryViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_shows_off_results_for_the_category(self):
        with mock.patch.object(
            openfoodfacts, "search_by_category", return_value=[RAW_OFF_PRODUCT]
        ):
            response = self.client.get(
                reverse("nutrition:food-category", args=["en:cereals"])
            )
        self.assertContains(response, "Test Muesli")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:food-category", args=["en:cereals"]))
        self.assertEqual(response.status_code, 302)


class FoodImportViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_imports_the_food_and_redirects_to_the_food_list_by_default(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT):
            response = self.client.post(
                reverse("nutrition:food-import"), {"off_barcode": "1234567890123"}
            )
        self.assertRedirects(response, reverse("nutrition:food-list"))
        self.assertTrue(Food.objects.filter(off_id="1234567890123").exists())

    def test_redirects_to_a_safe_next_url_when_given(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT):
            response = self.client.post(
                reverse("nutrition:food-import"),
                {
                    "off_barcode": "1234567890123",
                    "next": reverse("nutrition:food-category", args=["en:cereals"]),
                },
            )
        self.assertRedirects(response, reverse("nutrition:food-category", args=["en:cereals"]))

    def test_an_external_next_url_is_ignored(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT):
            response = self.client.post(
                reverse("nutrition:food-import"),
                {"off_barcode": "1234567890123", "next": "https://evil.example/"},
            )
        self.assertRedirects(response, reverse("nutrition:food-list"))

    def test_requires_post(self):
        response = self.client.get(reverse("nutrition:food-import"))
        self.assertEqual(response.status_code, 405)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.post(reverse("nutrition:food-import"))
        self.assertEqual(response.status_code, 302)


class OpenFoodFactsSettingsTests(TestCase):
    def test_defaults_to_enabled(self):
        self.assertTrue(OpenFoodFactsSettings.load().enabled)

    def test_is_a_singleton(self):
        first = OpenFoodFactsSettings.load()
        first.enabled = False
        first.save()
        second = OpenFoodFactsSettings.load()
        self.assertEqual(first.pk, second.pk)
        self.assertFalse(second.enabled)


class UserDeletionCascadeTests(TestCase):
    """Regression: several apps.nutrition FKs used on_delete=PROTECT
    on rows that are very commonly owned by the same user as the row
    referencing them (a food logged in its own owner's diary, etc.) —
    deleting that user tried to cascade both sides at once, which
    PROTECT blocked outright. See docs/NUTRITION.md and each field's
    own comment in models.py."""

    def test_deleting_a_user_with_a_full_nutrition_history_does_not_raise(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        food = make_food(alice)
        recipe = Recipe.objects.create(owner=alice, name="Bowl")
        RecipeIngredient.objects.create(recipe=recipe, food=food, quantity=Decimal("100"))
        slot = MealSlot.objects.create(name="Pre-workout", owner=alice)
        DiaryEntry.objects.create(
            user=alice, date=date(2026, 1, 1), meal_slot=slot, food=food,
            quantity=Decimal("100"),
        )
        goal = services.set_goal(
            alice, goal_type=GoalType.MAINTENANCE, target_rate_kg_per_week=Decimal("0")
        )
        breakdown = macros.calculate_macros(Decimal("80"), 2500, GoalType.MAINTENANCE)
        services.set_target(
            alice, goal=goal, daily_calories=2500, macro_breakdown=breakdown,
            source=TargetSource.CALCULATED, reason="",
        )
        plan = DietPlan.objects.create(
            user=alice, name="Plan", target_calories=2500,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"),
        )
        plan_meal = DietPlanMeal.objects.create(diet_plan=plan, meal_slot=slot, target_calories=500)
        from apps.nutrition.models import DietPlanItem

        DietPlanItem.objects.create(diet_plan_meal=plan_meal, food=food, quantity=Decimal("100"))

        alice.delete()  # must not raise ProtectedError
        self.assertFalse(User.objects.filter(username="alice").exists())


class OnboardingWizardTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def _complete_body_step(self):
        return self.client.post(
            reverse("nutrition:onboarding-body"),
            {
                "biological_sex": BiologicalSex.MALE,
                "birth_date": "1996-01-01",
                "height": "180",
                "weight": "80",
            },
        )

    def _complete_activity_step(self):
        return self.client.post(
            reverse("nutrition:onboarding-activity"),
            {
                "activity_job": ActivityJob.MODERATE,
                "daily_steps": "8000",
                "training_sessions_per_week": "4",
                "training_session_minutes": "60",
                "other_exercise_minutes_per_week": "0",
            },
        )

    def _complete_activity_level_step(self):
        return self.client.post(
            reverse("nutrition:onboarding-activity-level"),
            {"activity_level": ActivityLevel.MODERATE},
        )

    def _complete_goal_step(self):
        return self.client.post(
            reverse("nutrition:onboarding-goal"),
            {"goal_type": GoalType.FAT_LOSS_MODERATE, "target_weight": "74", "target_rate": "-0.5"},
        )

    def test_dashboard_redirects_to_onboarding_for_a_fresh_user(self):
        response = self.client.get(reverse("nutrition:dashboard"))
        self.assertRedirects(response, reverse("nutrition:onboarding-body"))

    def test_jumping_ahead_to_a_later_step_bounces_back_to_the_start(self):
        response = self.client.get(reverse("nutrition:onboarding-goal"))
        self.assertRedirects(response, reverse("nutrition:onboarding-body"))

    def test_the_full_wizard_creates_profile_goal_and_target(self):
        self._complete_body_step()
        self._complete_activity_step()
        self._complete_activity_level_step()
        self._complete_goal_step()
        review_get = self.client.get(reverse("nutrition:onboarding-review"))
        self.assertEqual(review_get.status_code, 200)
        self.assertContains(review_get, "2209")

        response = self.client.post(reverse("nutrition:onboarding-review"))
        self.assertRedirects(response, reverse("nutrition:dashboard"))

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.height, Decimal("1.8"))
        profile = NutritionProfile.objects.get(user=self.alice)
        self.assertEqual(profile.biological_sex, BiologicalSex.MALE)
        goal = NutritionGoal.objects.get(user=self.alice, ended_at__isnull=True)
        self.assertEqual(goal.target_weight, Decimal("74"))
        target = NutritionTarget.objects.get(user=self.alice, ended_at__isnull=True)
        self.assertEqual(target.daily_calories, 2209)
        self.assertTrue(
            BodyMeasurement.objects.filter(
                user=self.alice, measurement_type__name="Body weight", value=Decimal("80")
            ).exists()
        )

    def test_an_already_onboarded_user_is_redirected_away_from_every_step(self):
        self._complete_body_step()
        self._complete_activity_step()
        self._complete_activity_level_step()
        self._complete_goal_step()
        self.client.post(reverse("nutrition:onboarding-review"))

        for url_name in [
            "onboarding-body", "onboarding-activity", "onboarding-activity-level",
            "onboarding-goal", "onboarding-review",
        ]:
            response = self.client.get(reverse(f"nutrition:{url_name}"))
            self.assertRedirects(response, reverse("nutrition:dashboard"))

    def test_activity_level_step_shows_a_suggestion_derived_from_the_inputs(self):
        self._complete_body_step()
        self._complete_activity_step()
        response = self.client.get(reverse("nutrition:onboarding-activity-level"))
        self.assertContains(response, "Suggested")
        self.assertContains(response, "moderate job")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:onboarding-body"))
        self.assertEqual(response.status_code, 302)

    def test_invalid_body_step_data_redisplays_the_form_with_errors(self):
        response = self.client.post(
            reverse("nutrition:onboarding-body"),
            {"biological_sex": "male", "birth_date": "", "height": "180", "weight": "80"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "field-error")


class NutritionDashboardViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        NutritionProfile.objects.create(
            user=self.alice,
            biological_sex=BiologicalSex.MALE,
            birth_date=date(1996, 1, 1),
            activity_job=ActivityJob.MODERATE,
            activity_level=ActivityLevel.MODERATE,
        )
        self.goal = services.set_goal(
            self.alice, goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        breakdown = macros.calculate_macros(Decimal("80"), 2209, GoalType.FAT_LOSS_MODERATE)
        services.set_target(
            self.alice, goal=self.goal, daily_calories=2209, macro_breakdown=breakdown,
            source=TargetSource.CALCULATED, reason="test reason",
        )

    def test_shows_the_current_target_and_goal(self):
        response = self.client.get(reverse("nutrition:dashboard"))
        self.assertContains(response, "2209")
        self.assertContains(response, "test reason")

    def test_quick_links_reach_every_nutrition_section(self):
        response = self.client.get(reverse("nutrition:dashboard"))
        for url in [
            reverse("nutrition:diary-day"),
            reverse("nutrition:diary-add-entry"),
            reverse("nutrition:food-list"),
            reverse("nutrition:recipe-list"),
            reverse("nutrition:diet-plan-list"),
            reverse("nutrition:calculators-home"),
        ]:
            self.assertContains(response, url)


class NutritionComputationTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.chicken = make_food(self.alice, name="Chicken")
        self.rice = make_food(
            self.alice, name="Rice", calories=130, protein_grams=Decimal("2.7"),
            carbohydrate_grams=Decimal("28"), fat_grams=Decimal("0.3"),
        )

    def test_scale_nutrition_known_value(self):
        result = services.scale_nutrition(self.chicken, Decimal("150"))
        self.assertEqual(result.calories, Decimal("247.5"))
        self.assertEqual(result.protein_grams, Decimal("46.5"))
        self.assertEqual(result.fat_grams, Decimal("5.4"))

    def test_scale_nutrition_leaves_missing_optional_fields_none(self):
        result = services.scale_nutrition(self.chicken, Decimal("150"))
        self.assertIsNone(result.fiber_grams)

    def test_recipe_total_and_per_serving_known_values(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        RecipeIngredient.objects.create(
            recipe=recipe, food=self.chicken, quantity=Decimal("300")
        )
        RecipeIngredient.objects.create(recipe=recipe, food=self.rice, quantity=Decimal("200"))

        total = services.recipe_total_nutrition(recipe)
        self.assertEqual(total.calories, Decimal("755.0"))
        self.assertEqual(total.protein_grams, Decimal("98.4"))
        self.assertEqual(total.carbohydrate_grams, Decimal("56"))

        per_serving = services.recipe_per_serving_nutrition(recipe)
        self.assertEqual(per_serving.calories, Decimal("377.50"))
        self.assertEqual(per_serving.protein_grams, Decimal("49.20"))

    def test_diary_entry_nutrition_for_a_food_entry(self):
        entry = DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1),
            meal_slot=MealSlot.objects.get(name="Breakfast", owner=None),
            food=self.chicken, quantity=Decimal("150"),
        )
        result = services.diary_entry_nutrition(entry)
        self.assertEqual(result.calories, Decimal("247.5"))

    def test_diary_entry_nutrition_for_a_recipe_entry_uses_per_serving_times_quantity(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        RecipeIngredient.objects.create(
            recipe=recipe, food=self.chicken, quantity=Decimal("300")
        )
        RecipeIngredient.objects.create(recipe=recipe, food=self.rice, quantity=Decimal("200"))
        entry = DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1),
            meal_slot=MealSlot.objects.get(name="Breakfast", owner=None),
            recipe=recipe, quantity=Decimal("2"),  # 2 servings
        )
        result = services.diary_entry_nutrition(entry)
        self.assertEqual(result.calories, Decimal("755.00"))

    def test_daily_totals_sums_every_entry_for_that_date_only(self):
        breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        lunch = MealSlot.objects.get(name="Lunch", owner=None)
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=breakfast,
            food=self.chicken, quantity=Decimal("100"),
        )
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=lunch,
            food=self.rice, quantity=Decimal("100"),
        )
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 2), meal_slot=breakfast,
            food=self.chicken, quantity=Decimal("100"),
        )
        total = services.daily_totals(self.alice, date(2026, 1, 1))
        self.assertEqual(total.calories, Decimal("165") + Decimal("130"))

    def test_daily_totals_with_no_entries_is_zero_not_an_error(self):
        total = services.daily_totals(self.alice, date(2026, 1, 1))
        self.assertEqual(total.calories, Decimal("0"))


class MostUsedFoodsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.chicken = make_food(self.alice, name="Chicken")
        self.rice = make_food(self.alice, name="Rice")
        self.breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        self.lunch = MealSlot.objects.get(name="Lunch", owner=None)

    def test_ranks_by_how_often_a_food_was_logged_not_by_recency(self):
        # Chicken logged twice (older), rice logged once (more recent)
        # — chicken must still rank first, since this ranks by
        # frequency, not by which was eaten most recently.
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.breakfast,
            food=self.chicken, quantity=Decimal("100"),
        )
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 2), meal_slot=self.breakfast,
            food=self.chicken, quantity=Decimal("120"),
        )
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 3), meal_slot=self.lunch,
            food=self.rice, quantity=Decimal("200"),
        )
        usages = services.most_used_foods(self.alice)
        self.assertEqual([u.food for u in usages], [self.chicken, self.rice])

    def test_counts_usage_across_diary_recipes_and_diet_plans_combined(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=1)
        RecipeIngredient.objects.create(recipe=recipe, food=self.rice, quantity=Decimal("100"))
        plan = DietPlan.objects.create(
            user=self.alice, name="Plan", target_calories=2000,
            target_protein_grams=Decimal("100"), target_carbohydrate_grams=Decimal("200"),
            target_fat_grams=Decimal("60"),
        )
        meal = DietPlanMeal.objects.create(
            diet_plan=plan, meal_slot=self.breakfast, target_calories=500
        )
        DietPlanItem.objects.create(diet_plan_meal=meal, food=self.rice, quantity=Decimal("50"))
        DietPlanItem.objects.create(diet_plan_meal=meal, food=self.rice, quantity=Decimal("50"))
        # Rice: 1 recipe use + 2 diet-plan uses = 3. Chicken: never used.
        usages = services.most_used_foods(self.alice)
        self.assertEqual([u.food for u in usages], [self.rice])

    def test_prefills_the_most_recent_diary_quantity_and_meal_slot(self):
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.breakfast,
            food=self.chicken, quantity=Decimal("100"),
        )
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 2), meal_slot=self.lunch,
            food=self.chicken, quantity=Decimal("250"),
        )
        usages = services.most_used_foods(self.alice)
        self.assertEqual(usages[0].quantity, Decimal("250"))
        self.assertEqual(usages[0].meal_slot_id, self.lunch.pk)

    def test_falls_back_to_serving_size_for_a_food_never_logged_directly(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=1)
        RecipeIngredient.objects.create(recipe=recipe, food=self.rice, quantity=Decimal("100"))
        usages = services.most_used_foods(self.alice)
        self.assertEqual(usages[0].quantity, self.rice.serving_size)
        self.assertIsNone(usages[0].meal_slot_id)

    def test_never_counts_another_users_usage(self):
        DiaryEntry.objects.create(
            user=self.bob, date=date(2026, 1, 1), meal_slot=self.breakfast,
            food=self.chicken, quantity=Decimal("100"),
        )
        self.assertEqual(services.most_used_foods(self.alice), [])

    def test_no_usage_at_all_returns_an_empty_list(self):
        self.assertEqual(services.most_used_foods(self.alice), [])

    def test_respects_the_limit(self):
        foods = [make_food(self.alice, name=f"Food {i}") for i in range(5)]
        for food in foods:
            DiaryEntry.objects.create(
                user=self.alice, date=date(2026, 1, 1), meal_slot=self.breakfast,
                food=food, quantity=Decimal("100"),
            )
        self.assertEqual(len(services.most_used_foods(self.alice, limit=3)), 3)


class CopyDiaryDayTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.chicken = make_food(self.alice, name="Chicken")
        self.breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        self.lunch = MealSlot.objects.get(name="Lunch", owner=None)

    def test_copies_every_entry_from_the_source_day(self):
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.breakfast,
            food=self.chicken, quantity=Decimal("100"),
        )
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.lunch,
            food=self.chicken, quantity=Decimal("200"),
        )
        count = services.copy_diary_day(self.alice, date(2026, 1, 1), date(2026, 1, 5))
        self.assertEqual(count, 2)
        copied = DiaryEntry.objects.filter(user=self.alice, date=date(2026, 1, 5))
        self.assertEqual(copied.count(), 2)
        quantities = set(copied.values_list("quantity", flat=True))
        self.assertEqual(quantities, {Decimal("100"), Decimal("200")})

    def test_never_touches_the_source_day(self):
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.breakfast,
            food=self.chicken, quantity=Decimal("100"),
        )
        services.copy_diary_day(self.alice, date(2026, 1, 1), date(2026, 1, 5))
        source_day = DiaryEntry.objects.filter(user=self.alice, date=date(2026, 1, 1))
        self.assertEqual(source_day.count(), 1)

    def test_copying_an_empty_day_creates_nothing(self):
        count = services.copy_diary_day(self.alice, date(2026, 1, 1), date(2026, 1, 5))
        self.assertEqual(count, 0)
        self.assertFalse(DiaryEntry.objects.filter(user=self.alice, date=date(2026, 1, 5)).exists())

    def test_copying_onto_a_day_with_existing_entries_adds_rather_than_replaces(self):
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.breakfast,
            food=self.chicken, quantity=Decimal("100"),
        )
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 5), meal_slot=self.breakfast,
            food=self.chicken, quantity=Decimal("50"),
        )
        services.copy_diary_day(self.alice, date(2026, 1, 1), date(2026, 1, 5))
        target_day = DiaryEntry.objects.filter(user=self.alice, date=date(2026, 1, 5))
        self.assertEqual(target_day.count(), 2)


class CalorieHistoryAndStatsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.chicken = make_food(self.alice, name="Chicken")
        self.breakfast = MealSlot.objects.get(name="Breakfast", owner=None)

    def test_history_has_one_point_per_day_including_unlogged_days(self):
        history = services.calorie_history(self.alice, days=7)
        self.assertEqual(len(history), 7)
        self.assertTrue(all(totals.calories == Decimal("0") for _day, totals in history))

    def test_history_is_oldest_first_and_reflects_logged_totals(self):
        today = timezone.localdate()
        DiaryEntry.objects.create(
            user=self.alice, date=today, meal_slot=self.breakfast,
            food=self.chicken, quantity=Decimal("100"),
        )
        history = services.calorie_history(self.alice, days=7)
        self.assertEqual(history[0][0], today - timedelta(days=6))
        self.assertEqual(history[-1][0], today)
        self.assertEqual(history[-1][1].calories, Decimal("165"))

    def test_stats_with_no_logged_days_returns_all_zero_not_an_error(self):
        summary = services.nutrition_stats(self.alice, days=7)
        self.assertEqual(summary.days_logged, 0)
        self.assertEqual(summary.average_calories, Decimal("0"))

    def test_stats_averages_only_over_days_something_was_logged(self):
        today = timezone.localdate()
        DiaryEntry.objects.create(
            user=self.alice, date=today, meal_slot=self.breakfast,
            food=self.chicken, quantity=Decimal("200"),  # 330 kcal
        )
        summary = services.nutrition_stats(self.alice, days=7)
        # Only 1 of the 7 days had anything logged — the average must be
        # that one day's own total, not diluted by the other 6 empty days.
        self.assertEqual(summary.days_logged, 1)
        self.assertEqual(summary.average_calories, Decimal("330"))


class FoodListViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_shows_own_and_shared_foods_but_not_another_users_private_food(self):
        make_food(self.alice, name="Alice's food")
        make_food(None, name="Shared food")
        make_food(self.bob, name="Bob's private food")
        response = self.client.get(reverse("nutrition:food-list"))
        names = [f.name for f in response.context["foods"]]
        self.assertIn("Alice's food", names)
        self.assertIn("Shared food", names)
        self.assertNotIn("Bob's private food", names)

    def test_search_filters_by_name(self):
        make_food(self.alice, name="Chicken breast")
        make_food(self.alice, name="Rice")
        response = self.client.get(reverse("nutrition:food-list"), {"q": "chicken"})
        names = [f.name for f in response.context["foods"]]
        self.assertEqual(names, ["Chicken breast"])

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:food-list"))
        self.assertEqual(response.status_code, 302)

    def test_the_back_link_returns_to_the_nutrition_dashboard(self):
        """Regression: this used to link "back" to the food diary
        instead — a leftover from before this page was also reachable
        directly from the dashboard's own "Quick links" card, so
        arriving that way and clicking "back" landed somewhere the
        user never came from."""
        response = self.client.get(reverse("nutrition:food-list"))
        self.assertContains(response, "Back to nutrition")


class FoodCreateViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_creates_a_food_owned_by_the_current_user(self):
        response = self.client.post(
            reverse("nutrition:food-create"),
            {
                "name": "Oatmeal", "brand": "", "serving_size": "40", "serving_unit": "g",
                "calories": "150", "protein_grams": "5", "carbohydrate_grams": "27",
                "fat_grams": "3",
            },
        )
        self.assertEqual(response.status_code, 302)
        food = Food.objects.get(name="Oatmeal")
        self.assertEqual(food.owner, self.alice)


class FoodSearchResultsViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_a_blank_query_returns_no_results(self):
        make_food(self.alice, name="Chicken")
        response = self.client.get(reverse("nutrition:food-search"), {"q": ""})
        self.assertNotContains(response, "Chicken")

    def test_finds_a_local_match(self):
        make_food(self.alice, name="Chicken breast")
        with mock.patch.object(openfoodfacts, "search_products", return_value=[]):
            response = self.client.get(reverse("nutrition:food-search"), {"q": "chicken"})
        self.assertContains(response, "Chicken breast")


class DiaryAddEntryViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        self.food = make_food(self.alice)
        self.slot = MealSlot.objects.get(name="Breakfast", owner=None)

    def test_the_camera_barcode_scanner_is_wired_up(self):
        response = self.client.get(reverse("nutrition:diary-add-entry"))
        self.assertContains(response, "barcode-scanner.js")
        self.assertContains(response, "ironstackBarcodeScanner()")
        self.assertContains(response, "Scan barcode")

    def test_reaches_the_add_entry_view_not_a_405(self):
        """Regression: diary/<str:target_date>/ used to be registered
        before diary/add/, so a POST to diary/add/ matched the former
        (target_date="add", no post() method there) and 405'd instead
        of ever reaching DiaryAddEntryView. See urls.py's own comment."""
        response = self.client.post(
            reverse("nutrition:diary-add-entry"),
            {
                "food_id": self.food.pk, "meal_slot": self.slot.pk,
                "quantity": "150", "date": "2026-01-01",
            },
        )
        self.assertNotEqual(response.status_code, 405)

    def test_adding_a_local_food_creates_a_diary_entry(self):
        self.client.post(
            reverse("nutrition:diary-add-entry"),
            {
                "food_id": self.food.pk, "meal_slot": self.slot.pk,
                "quantity": "150", "date": "2026-01-01",
            },
        )
        entry = DiaryEntry.objects.get(user=self.alice)
        self.assertEqual(entry.food, self.food)
        self.assertEqual(entry.quantity, Decimal("150"))
        self.assertEqual(entry.date, date(2026, 1, 1))

    def test_adding_an_off_result_imports_and_logs_it(self):
        with mock.patch.object(openfoodfacts, "get_product", return_value=RAW_OFF_PRODUCT):
            response = self.client.post(
                reverse("nutrition:diary-add-entry"),
                {
                    "off_barcode": "1234567890123", "meal_slot": self.slot.pk,
                    "quantity": "50", "date": "2026-01-01",
                },
            )
        self.assertEqual(response.status_code, 302)
        entry = DiaryEntry.objects.get(user=self.alice)
        self.assertEqual(entry.food.off_id, "1234567890123")

    def test_neither_food_nor_barcode_redisplays_the_form_with_an_error(self):
        response = self.client.post(
            reverse("nutrition:diary-add-entry"),
            {"meal_slot": self.slot.pk, "quantity": "150", "date": "2026-01-01"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "field-error")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:diary-add-entry"))
        self.assertEqual(response.status_code, 302)

    def test_shows_a_most_used_entry_for_a_previously_logged_food(self):
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.slot,
            food=self.food, quantity=Decimal("150"),
        )
        response = self.client.get(reverse("nutrition:diary-add-entry"))
        self.assertContains(response, "Most used")
        self.assertContains(response, self.food.name)

    def test_no_most_used_section_for_a_brand_new_user(self):
        response = self.client.get(reverse("nutrition:diary-add-entry"))
        self.assertNotContains(response, "Most used")

    def test_tapping_a_most_used_food_creates_a_new_entry_with_the_same_details(self):
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.slot,
            food=self.food, quantity=Decimal("150"),
        )
        response = self.client.post(
            reverse("nutrition:diary-add-entry"),
            {
                "food_id": self.food.pk, "meal_slot": self.slot.pk,
                "quantity": "150", "date": "2026-02-02",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            DiaryEntry.objects.filter(
                user=self.alice, date=date(2026, 2, 2), quantity=Decimal("150")
            ).exists()
        )


class DiaryDayCopyViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.food = make_food(self.alice)
        self.slot = MealSlot.objects.get(name="Breakfast", owner=None)
        self.client.login(username="alice", password="s3cret-pass")

    def test_copies_entries_and_redirects_to_the_target_date(self):
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.slot,
            food=self.food, quantity=Decimal("100"),
        )
        response = self.client.post(
            reverse("nutrition:diary-day-copy", kwargs={"source_date": "2026-01-01"}),
            {"target_date": "2026-01-05"},
        )
        self.assertRedirects(
            response, reverse("nutrition:diary-day", kwargs={"target_date": "2026-01-05"})
        )
        self.assertTrue(
            DiaryEntry.objects.filter(user=self.alice, date=date(2026, 1, 5)).exists()
        )

    def test_missing_target_date_shows_an_error_and_changes_nothing(self):
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.slot,
            food=self.food, quantity=Decimal("100"),
        )
        response = self.client.post(
            reverse("nutrition:diary-day-copy", kwargs={"source_date": "2026-01-01"}), {}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DiaryEntry.objects.filter(user=self.alice).count(), 1)

    def test_requires_post(self):
        response = self.client.get(
            reverse("nutrition:diary-day-copy", kwargs={"source_date": "2026-01-01"})
        )
        self.assertEqual(response.status_code, 405)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.post(
            reverse("nutrition:diary-day-copy", kwargs={"source_date": "2026-01-01"}),
            {"target_date": "2026-01-05"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(DiaryEntry.objects.filter(date=date(2026, 1, 5)).exists())


class NutritionStatsViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.food = make_food(self.alice)
        self.slot = MealSlot.objects.get(name="Breakfast", owner=None)
        self.client.login(username="alice", password="s3cret-pass")

    def test_renders_the_chart_and_summary_for_a_user_with_history(self):
        DiaryEntry.objects.create(
            user=self.alice, date=timezone.localdate(), meal_slot=self.slot,
            food=self.food, quantity=Decimal("100"),
        )
        response = self.client.get(reverse("nutrition:stats"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"].days_logged, 1)
        self.assertIsNotNone(response.context["calorie_chart"])

    def test_renders_without_error_for_a_brand_new_user(self):
        response = self.client.get(reverse("nutrition:stats"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"].days_logged, 0)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:stats"))
        self.assertEqual(response.status_code, 302)


class DiaryEntryEditDeleteViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.food = make_food(self.alice)
        self.slot = MealSlot.objects.get(name="Breakfast", owner=None)
        self.entry = DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.slot,
            food=self.food, quantity=Decimal("100"),
        )
        self.client.login(username="alice", password="s3cret-pass")

    def test_editing_updates_the_quantity(self):
        response = self.client.post(
            reverse("nutrition:diary-entry-edit", args=[self.entry.pk]),
            {"quantity": "200", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.quantity, Decimal("200"))

    def test_deleting_removes_the_entry(self):
        response = self.client.post(
            reverse("nutrition:diary-entry-delete", args=[self.entry.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(DiaryEntry.objects.filter(pk=self.entry.pk).exists())

    def test_another_users_entry_is_a_404_not_a_403(self):
        self.client.logout()
        self.client.login(username="bob", password="s3cret-pass")
        response = self.client.get(reverse("nutrition:diary-entry-edit", args=[self.entry.pk]))
        self.assertEqual(response.status_code, 404)

    def test_delete_requires_post(self):
        response = self.client.get(reverse("nutrition:diary-entry-delete", args=[self.entry.pk]))
        self.assertEqual(response.status_code, 405)

    def test_anonymous_access_redirects_rather_than_500ing(self):
        """Regression: these plain function views had no login
        protection at all -- an anonymous request tried to filter
        DiaryEntry.objects.get(..., user=AnonymousUser()), which isn't
        a real model instance Django's ORM can extract a pk from,
        crashing with a 500 instead of redirecting to login."""
        self.client.logout()
        edit_response = self.client.get(
            reverse("nutrition:diary-entry-edit", args=[self.entry.pk])
        )
        self.assertEqual(edit_response.status_code, 302)
        delete_response = self.client.post(
            reverse("nutrition:diary-entry-delete", args=[self.entry.pk])
        )
        self.assertEqual(delete_response.status_code, 302)


class DiaryDayViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_shows_entries_grouped_under_the_right_meal_slot(self):
        food = make_food(self.alice)
        breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=breakfast,
            food=food, quantity=Decimal("100"),
        )
        response = self.client.get(
            reverse("nutrition:diary-day", kwargs={"target_date": "2026-01-01"})
        )
        breakfast_context = next(
            s for s in response.context["meal_slots"] if s.pk == breakfast.pk
        )
        self.assertEqual(len(breakfast_context.entries), 1)

    def test_defaults_to_today_with_no_date_given(self):
        response = self.client.get(reverse("nutrition:diary-day"))
        self.assertEqual(response.context["date"], timezone.localdate())

    def test_shows_a_date_picker_prefilled_with_the_current_page_date(self):
        response = self.client.get(
            reverse("nutrition:diary-day", kwargs={"target_date": "2026-01-01"})
        )
        self.assertContains(response, 'type="date" value="2026-01-01"')

    def test_an_invalid_date_falls_back_to_today_rather_than_crashing(self):
        response = self.client.get(
            reverse("nutrition:diary-day", kwargs={"target_date": "not-a-date"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["date"], timezone.localdate())

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:diary-day"))
        self.assertEqual(response.status_code, 302)

    def test_has_a_way_back_to_the_nutrition_dashboard(self):
        """Regression: the diary day used to have no link back to the
        nutrition dashboard at all — reachable directly from the
        dashboard itself (Quick links, "+ Log food now"), it needs the
        same "back to nutrition" affordance every other nutrition
        section already has."""
        response = self.client.get(reverse("nutrition:diary-day"))
        self.assertContains(response, "Back to nutrition")


class RecipeViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.chicken = make_food(self.alice, name="Chicken")
        self.rice = make_food(
            self.alice, name="Rice", calories=130, protein_grams=Decimal("2.7"),
            carbohydrate_grams=Decimal("28"), fat_grams=Decimal("0.3"),
        )
        self.client.login(username="alice", password="s3cret-pass")

    def test_recipe_create(self):
        response = self.client.post(
            reverse("nutrition:recipe-create"),
            {"name": "Bowl", "servings": "2", "instructions": ""},
        )
        self.assertEqual(response.status_code, 302)
        recipe = Recipe.objects.get(name="Bowl")
        self.assertEqual(recipe.owner, self.alice)

    def test_creating_a_recipe_shows_a_message_pointing_at_adding_ingredients(self):
        response = self.client.post(
            reverse("nutrition:recipe-create"),
            {"name": "Bowl", "servings": "2", "instructions": ""},
            follow=True,
        )
        self.assertContains(response, "add its ingredients")

    def test_recipe_list_only_shows_the_owners_own_recipes(self):
        Recipe.objects.create(owner=self.alice, name="Alice's Bowl", servings=1)
        Recipe.objects.create(owner=self.bob, name="Bob's Bowl", servings=1)
        response = self.client.get(reverse("nutrition:recipe-list"))
        names = [r.name for r in response.context["recipes"]]
        self.assertIn("Alice's Bowl", names)
        self.assertNotIn("Bob's Bowl", names)

    def test_recipe_list_search_filters_by_name(self):
        Recipe.objects.create(owner=self.alice, name="Chicken Bowl", servings=1)
        Recipe.objects.create(owner=self.alice, name="Rice Salad", servings=1)
        response = self.client.get(reverse("nutrition:recipe-list"), {"q": "chicken"})
        names = [r.name for r in response.context["recipes"]]
        self.assertEqual(names, ["Chicken Bowl"])

    def test_recipe_list_shows_calories_per_serving(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        RecipeIngredient.objects.create(
            recipe=recipe, food=self.chicken, quantity=Decimal("300")
        )
        response = self.client.get(reverse("nutrition:recipe-list"))
        self.assertContains(response, "248 kcal/serving")

    def test_the_back_link_returns_to_the_nutrition_dashboard(self):
        """Regression: this used to link "back" to the food diary
        instead of the dashboard the recipes list is now also directly
        reachable from (Quick links)."""
        response = self.client.get(reverse("nutrition:recipe-list"))
        self.assertContains(response, "Back to nutrition")

    def test_another_users_recipe_detail_is_a_404(self):
        recipe = Recipe.objects.create(owner=self.bob, name="Bob's Bowl", servings=1)
        response = self.client.get(reverse("nutrition:recipe-detail", args=[recipe.pk]))
        self.assertEqual(response.status_code, 404)

    def test_the_camera_barcode_scanner_is_wired_up_on_the_ingredient_search_page(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        response = self.client.get(
            reverse("nutrition:recipe-ingredient-create", args=[recipe.pk])
        )
        self.assertContains(response, "barcode-scanner.js")
        self.assertContains(response, "ironstackBarcodeScanner()")

    def test_the_ingredient_search_page_shows_most_used_foods(self):
        other_recipe = Recipe.objects.create(owner=self.alice, name="Other", servings=1)
        RecipeIngredient.objects.create(
            recipe=other_recipe, food=self.chicken, quantity=Decimal("100")
        )
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        response = self.client.get(
            reverse("nutrition:recipe-ingredient-create", args=[recipe.pk])
        )
        self.assertContains(response, "Most used")
        self.assertContains(response, self.chicken.name)

    def test_adding_and_removing_an_ingredient(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        response = self.client.post(
            reverse("nutrition:recipe-ingredient-create", args=[recipe.pk]),
            {"food_id": self.chicken.pk, "quantity": "300"},
        )
        self.assertEqual(response.status_code, 302)
        ingredient = RecipeIngredient.objects.get(recipe=recipe)
        self.assertEqual(ingredient.food, self.chicken)

        response = self.client.post(
            reverse("nutrition:recipe-ingredient-delete", args=[recipe.pk, ingredient.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(RecipeIngredient.objects.filter(pk=ingredient.pk).exists())

    def test_editing_an_ingredients_quantity(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        ingredient = RecipeIngredient.objects.create(
            recipe=recipe, food=self.chicken, quantity=Decimal("300")
        )
        response = self.client.post(
            reverse("nutrition:recipe-ingredient-edit", args=[recipe.pk, ingredient.pk]),
            {"quantity": "250"},
        )
        self.assertEqual(response.status_code, 302)
        ingredient.refresh_from_db()
        self.assertEqual(ingredient.quantity, Decimal("250"))

    def test_editing_an_ingredient_keeps_its_original_position(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        first = RecipeIngredient.objects.create(
            recipe=recipe, food=self.chicken, quantity=Decimal("300"), order=0
        )
        second = RecipeIngredient.objects.create(
            recipe=recipe, food=self.rice, quantity=Decimal("200"), order=1
        )
        self.client.post(
            reverse("nutrition:recipe-ingredient-edit", args=[recipe.pk, first.pk]),
            {"quantity": "250"},
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.order, 0)
        self.assertEqual(second.order, 1)

    def test_editing_another_users_ingredient_is_a_404(self):
        recipe = Recipe.objects.create(owner=self.bob, name="Bob's Bowl", servings=1)
        ingredient = RecipeIngredient.objects.create(
            recipe=recipe, food=self.chicken, quantity=Decimal("300")
        )
        response = self.client.get(
            reverse("nutrition:recipe-ingredient-edit", args=[recipe.pk, ingredient.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_adding_an_ingredient_from_openfoodfacts_imports_and_links_it(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        raw_product = {
            "code": "5901234123457",
            "product_name": "Test Product",
            "brands": "Test Brand",
            "nutriments": {
                "energy-kcal_100g": 200,
                "proteins_100g": 10,
                "carbohydrates_100g": 20,
                "fat_100g": 5,
            },
        }
        with mock.patch.object(openfoodfacts, "get_product", return_value=raw_product):
            response = self.client.post(
                reverse("nutrition:recipe-ingredient-create", args=[recipe.pk]),
                {"off_barcode": "5901234123457", "quantity": "150"},
            )
        self.assertEqual(response.status_code, 302)
        ingredient = RecipeIngredient.objects.get(recipe=recipe)
        self.assertEqual(ingredient.food.off_id, "5901234123457")
        self.assertEqual(ingredient.food.name, "Test Product")

    def test_search_results_partial_posts_to_the_recipe_ingredient_endpoint_in_recipe_mode(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        response = self.client.get(
            reverse("nutrition:food-search"),
            {"q": "Chicken", "mode": "recipe", "recipe_pk": recipe.pk},
        )
        self.assertContains(
            response, reverse("nutrition:recipe-ingredient-create", args=[recipe.pk])
        )

    def test_recipe_detail_shows_correct_totals(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        RecipeIngredient.objects.create(
            recipe=recipe, food=self.chicken, quantity=Decimal("300")
        )
        RecipeIngredient.objects.create(recipe=recipe, food=self.rice, quantity=Decimal("200"))
        response = self.client.get(reverse("nutrition:recipe-detail", args=[recipe.pk]))
        self.assertEqual(response.context["total"].calories, Decimal("755.0"))
        self.assertEqual(response.context["per_serving"].calories, Decimal("377.50"))

    def test_recipe_detail_shows_fiber_when_an_ingredient_has_it(self):
        fibrous = make_food(self.alice, name="Beans", fiber_grams=Decimal("6"))
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=1)
        RecipeIngredient.objects.create(recipe=recipe, food=fibrous, quantity=Decimal("100"))
        response = self.client.get(reverse("nutrition:recipe-detail", args=[recipe.pk]))
        self.assertContains(response, "Fiber")

    def test_recipe_detail_hides_optional_nutrients_when_no_ingredient_has_them(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=1)
        RecipeIngredient.objects.create(
            recipe=recipe, food=self.chicken, quantity=Decimal("100")
        )
        response = self.client.get(reverse("nutrition:recipe-detail", args=[recipe.pk]))
        self.assertNotContains(response, "<td>Fiber</td>")

    def test_logging_a_recipe_creates_a_diary_entry(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        RecipeIngredient.objects.create(
            recipe=recipe, food=self.chicken, quantity=Decimal("300")
        )
        slot = MealSlot.objects.get(name="Lunch", owner=None)
        response = self.client.post(
            reverse("nutrition:recipe-log", args=[recipe.pk]),
            {
                "meal_slot": slot.pk, "quantity": "1",
                "date": timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)
        entry = DiaryEntry.objects.get(user=self.alice, recipe=recipe)
        self.assertEqual(entry.meal_slot, slot)
        self.assertEqual(entry.date, timezone.localdate())

    def test_a_recipe_can_be_logged_to_a_different_day(self):
        # Regression: this used to be hardcoded to today with no way
        # to log a recipe eaten yesterday or planned for tomorrow.
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        RecipeIngredient.objects.create(
            recipe=recipe, food=self.chicken, quantity=Decimal("300")
        )
        slot = MealSlot.objects.get(name="Lunch", owner=None)
        yesterday = timezone.localdate() - timedelta(days=1)
        response = self.client.post(
            reverse("nutrition:recipe-log", args=[recipe.pk]),
            {"meal_slot": slot.pk, "quantity": "1", "date": yesterday.isoformat()},
        )
        self.assertEqual(response.status_code, 302)
        entry = DiaryEntry.objects.get(user=self.alice, recipe=recipe)
        self.assertEqual(entry.date, yesterday)

    def test_deleting_a_recipe_that_does_not_belong_to_the_user_is_a_404(self):
        recipe = Recipe.objects.create(owner=self.bob, name="Bob's Bowl", servings=1)
        response = self.client.post(reverse("nutrition:recipe-delete", args=[recipe.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Recipe.objects.filter(pk=recipe.pk).exists())

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:recipe-list"))
        self.assertEqual(response.status_code, 302)

    def test_the_plain_function_views_also_require_login(self):
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        self.client.logout()
        for response in [
            self.client.get(reverse("nutrition:recipe-create")),
            self.client.get(reverse("nutrition:recipe-update", args=[recipe.pk])),
            self.client.post(reverse("nutrition:recipe-delete", args=[recipe.pk])),
            self.client.get(reverse("nutrition:recipe-ingredient-create", args=[recipe.pk])),
        ]:
            self.assertEqual(response.status_code, 302)


class SplitCaloriesEvenlyTests(TestCase):
    def test_splits_evenly_with_no_remainder(self):
        self.assertEqual(diet_builder.split_calories_evenly(2400, 3), [800, 800, 800])

    def test_the_remainder_is_absorbed_by_the_last_share(self):
        shares = diet_builder.split_calories_evenly(2209, 3)
        self.assertEqual(shares, [736, 736, 737])
        self.assertEqual(sum(shares), 2209)

    def test_zero_meals_returns_an_empty_list(self):
        self.assertEqual(diet_builder.split_calories_evenly(2000, 0), [])


class SuggestItemForCalorieBudgetTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_no_foods_or_recipes_returns_none(self):
        self.assertIsNone(diet_builder.suggest_item_for_calorie_budget(self.alice, Decimal("500")))

    def test_picks_the_closest_natural_match_and_scales_it_exactly(self):
        make_food(self.alice, name="Chicken", calories=165)
        make_food(self.alice, name="Oatmeal", calories=150)
        make_food(self.alice, name="Yogurt", calories=100)
        result = diet_builder.suggest_item_for_calorie_budget(self.alice, Decimal("736"))
        self.assertEqual(result.food.name, "Chicken")
        self.assertEqual(result.quantity, Decimal("446.06"))

    def test_a_recipe_can_be_the_suggestion_too(self):
        chicken = make_food(self.alice, name="Chicken", calories=165)
        recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=1)
        RecipeIngredient.objects.create(recipe=recipe, food=chicken, quantity=Decimal("500"))
        # per-serving calories for this 1-serving recipe = 825 -- much
        # closer to a 800kcal budget than any bare food would be.
        make_food(self.alice, name="Snack bar", calories=200)
        result = diet_builder.suggest_item_for_calorie_budget(self.alice, Decimal("800"))
        self.assertEqual(result.recipe, recipe)
        self.assertIsNone(result.food)

    def test_shared_foods_are_eligible_too(self):
        make_food(None, name="Shared chicken", calories=165)
        result = diet_builder.suggest_item_for_calorie_budget(self.alice, Decimal("500"))
        self.assertIsNotNone(result)
        self.assertEqual(result.food.name, "Shared chicken")


class BuildAndApplyDietPlanTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.chicken = make_food(self.alice, name="Chicken", calories=165)
        self.breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        self.lunch = MealSlot.objects.get(name="Lunch", owner=None)

    def test_build_creates_one_meal_per_slot_with_a_suggested_item(self):
        plan = diet_builder.build_diet_plan(
            self.alice,
            name="Test plan",
            goal=None,
            target_calories=1600,
            target_protein_grams=Decimal("120"),
            target_carbohydrate_grams=Decimal("150"),
            target_fat_grams=Decimal("40"),
            meal_slots=[self.breakfast, self.lunch],
        )
        self.assertEqual(plan.meals.count(), 2)
        for meal in plan.meals.all():
            self.assertEqual(meal.items.count(), 1)
        self.assertEqual(sum(m.target_calories for m in plan.meals.all()), 1600)

    def test_building_a_new_plan_deactivates_the_previous_one(self):
        first = diet_builder.build_diet_plan(
            self.alice, name="First", goal=None, target_calories=1600,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        second = diet_builder.build_diet_plan(
            self.alice, name="Second", goal=None, target_calories=1800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)

    def test_a_meal_slot_with_no_available_foods_still_gets_created_with_no_items(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        plan = diet_builder.build_diet_plan(
            bob, name="Empty", goal=None, target_calories=1600,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        self.assertEqual(plan.meals.count(), 1)
        self.assertEqual(plan.meals.first().items.count(), 0)

    def test_apply_creates_a_diary_entry_per_item(self):
        plan = diet_builder.build_diet_plan(
            self.alice, name="Test plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast, self.lunch],
        )
        created = diet_builder.apply_diet_plan(plan, date(2026, 1, 1))
        self.assertEqual(len(created), 2)
        entries = DiaryEntry.objects.filter(user=self.alice, date=date(2026, 1, 1))
        self.assertEqual(entries.count(), 2)

    def test_applying_a_plan_twice_does_not_touch_the_plan_itself(self):
        plan = diet_builder.build_diet_plan(
            self.alice, name="Test plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        diet_builder.apply_diet_plan(plan, date(2026, 1, 1))
        diet_builder.apply_diet_plan(plan, date(2026, 1, 2))
        self.assertEqual(plan.meals.first().items.count(), 1)
        self.assertEqual(DiaryEntry.objects.filter(user=self.alice).count(), 2)


class DietPlanViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.chicken = make_food(self.alice, name="Chicken", calories=165)
        self.breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        self.client.login(username="alice", password="s3cret-pass")

    def test_create_view_prefills_from_the_active_target(self):
        goal = services.set_goal(
            self.alice, goal_type=GoalType.MAINTENANCE, target_rate_kg_per_week=Decimal("0")
        )
        breakdown = macros.calculate_macros(Decimal("80"), 2500, GoalType.MAINTENANCE)
        services.set_target(
            self.alice, goal=goal, daily_calories=2500, macro_breakdown=breakdown,
            source=TargetSource.CALCULATED, reason="",
        )
        response = self.client.get(reverse("nutrition:diet-plan-create"))
        self.assertContains(response, 'value="2500"')

    def test_posting_creates_a_plan_and_redirects_to_its_detail_page(self):
        response = self.client.post(
            reverse("nutrition:diet-plan-create"),
            {
                "name": "My plan", "target_calories": "1600",
                "target_protein_grams": "120", "target_carbohydrate_grams": "150",
                "target_fat_grams": "40", "meal_slots": [self.breakfast.pk],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DietPlan.objects.filter(user=self.alice, name="My plan").exists())

    def test_another_users_plan_is_a_404(self):
        plan = DietPlan.objects.create(
            user=self.bob, name="Bob's plan", target_calories=2000,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"),
        )
        response = self.client.get(reverse("nutrition:diet-plan-detail", args=[plan.pk]))
        self.assertEqual(response.status_code, 404)

    def test_swapping_an_item_updates_it(self):
        plan = diet_builder.build_diet_plan(
            self.alice, name="Plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        item = plan.meals.first().items.first()
        other_food = make_food(self.alice, name="Rice", calories=130)
        response = self.client.post(
            reverse("nutrition:diet-plan-item-edit", args=[plan.pk, item.pk]),
            {"food": other_food.pk, "quantity": "200"},
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.food, other_food)

    def test_adding_an_extra_item_to_a_meal_does_not_replace_the_generated_one(self):
        plan = diet_builder.build_diet_plan(
            self.alice, name="Plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        meal = plan.meals.first()
        self.assertEqual(meal.items.count(), 1)
        extra_food = make_food(self.alice, name="Rice", calories=130)
        response = self.client.post(
            reverse("nutrition:diet-plan-meal-item-add", args=[plan.pk, meal.pk]),
            {"food_id": extra_food.pk, "quantity": "200"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(meal.items.count(), 2)
        self.assertTrue(meal.items.filter(food=extra_food).exists())

    def test_the_camera_barcode_scanner_is_wired_up_on_the_add_item_page(self):
        plan = diet_builder.build_diet_plan(
            self.alice, name="Plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        meal = plan.meals.first()
        response = self.client.get(
            reverse("nutrition:diet-plan-meal-item-add", args=[plan.pk, meal.pk])
        )
        self.assertContains(response, "barcode-scanner.js")
        self.assertContains(response, "ironstackBarcodeScanner()")

    def test_the_add_item_page_shows_most_used_foods(self):
        rice = make_food(self.alice, name="Rice", calories=130)
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.breakfast,
            food=rice, quantity=Decimal("100"),
        )
        plan = diet_builder.build_diet_plan(
            self.alice, name="Plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        meal = plan.meals.first()
        response = self.client.get(
            reverse("nutrition:diet-plan-meal-item-add", args=[plan.pk, meal.pk])
        )
        self.assertContains(response, "Most used")
        self.assertContains(response, "Rice")

    def test_deleting_an_item_removes_only_that_item(self):
        plan = diet_builder.build_diet_plan(
            self.alice, name="Plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        meal = plan.meals.first()
        original_item = meal.items.first()
        extra_food = make_food(self.alice, name="Rice", calories=130)
        extra_item = meal.items.create(food=extra_food, quantity=Decimal("200"), order=1)
        response = self.client.post(
            reverse("nutrition:diet-plan-item-delete", args=[plan.pk, extra_item.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(meal.items.filter(pk=extra_item.pk).exists())
        self.assertTrue(meal.items.filter(pk=original_item.pk).exists())

    def test_deleting_another_users_item_is_a_404(self):
        make_food(self.bob, name="Bob's Chicken", calories=165)
        plan = diet_builder.build_diet_plan(
            self.bob, name="Bob's plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        item = plan.meals.first().items.first()
        response = self.client.post(
            reverse("nutrition:diet-plan-item-delete", args=[plan.pk, item.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(DietPlanItem.objects.filter(pk=item.pk).exists())

    def test_logging_the_plan_creates_diary_entries(self):
        plan = diet_builder.build_diet_plan(
            self.alice, name="Plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        response = self.client.post(
            reverse("nutrition:diet-plan-log", args=[plan.pk]), {"date": "2026-01-01"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            DiaryEntry.objects.filter(user=self.alice, date=date(2026, 1, 1)).exists()
        )

    def test_an_invalid_date_shows_the_form_error_instead_of_silently_doing_nothing(self):
        plan = diet_builder.build_diet_plan(
            self.alice, name="Plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        response = self.client.post(
            reverse("nutrition:diet-plan-log", args=[plan.pk]), {"date": "not-a-date"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["log_form"].errors)
        self.assertFalse(DiaryEntry.objects.filter(user=self.alice).exists())

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:diet-plan-list"))
        self.assertEqual(response.status_code, 302)

    def test_the_back_link_returns_to_the_nutrition_dashboard(self):
        """Regression: this used to link "back" to the food diary
        instead of the dashboard the diet-plan list is now also
        directly reachable from (Quick links)."""
        response = self.client.get(reverse("nutrition:diet-plan-list"))
        self.assertContains(response, "Back to nutrition")

    def test_the_plain_function_views_also_require_login(self):
        plan = diet_builder.build_diet_plan(
            self.alice, name="Plan", goal=None, target_calories=800,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"), meal_slots=[self.breakfast],
        )
        item = plan.meals.first().items.first()
        self.client.logout()
        for response in [
            self.client.post(reverse("nutrition:diet-plan-delete", args=[plan.pk])),
            self.client.get(reverse("nutrition:diet-plan-item-edit", args=[plan.pk, item.pk])),
            self.client.post(reverse("nutrition:diet-plan-log", args=[plan.pk])),
        ]:
            self.assertEqual(response.status_code, 302)


class IsTrainingDayTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_false_with_no_sessions_at_all(self):
        self.assertFalse(services.is_training_day(self.alice, date(2026, 1, 1)))

    def test_true_for_a_completed_session_that_day(self):
        from apps.workouts.services import complete_session, start_session

        session = start_session(self.alice, workout=None)
        session.started_at = timezone.make_aware(
            timezone.datetime(2026, 1, 1, 10, 0)
        )
        session.save(update_fields=["started_at"])
        complete_session(session)
        self.assertTrue(services.is_training_day(self.alice, date(2026, 1, 1)))

    def test_false_for_a_session_that_was_never_completed(self):
        from apps.workouts.services import start_session

        session = start_session(self.alice, workout=None)
        session.started_at = timezone.make_aware(
            timezone.datetime(2026, 1, 1, 10, 0)
        )
        session.save(update_fields=["started_at"])
        self.assertFalse(services.is_training_day(self.alice, date(2026, 1, 1)))


class NutritionDashboardExtrasTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        NutritionProfile.objects.create(
            user=self.alice, biological_sex=BiologicalSex.MALE, birth_date=date(1996, 1, 1),
            activity_job=ActivityJob.MODERATE, activity_level=ActivityLevel.MODERATE,
        )

    def test_shows_rest_day_with_no_workout_session(self):
        response = self.client.get(reverse("nutrition:dashboard"))
        self.assertContains(response, "Rest day")

    def test_shows_training_day_with_a_completed_session_today(self):
        from apps.workouts.services import complete_session, start_session

        session = start_session(self.alice, workout=None)
        complete_session(session)
        response = self.client.get(reverse("nutrition:dashboard"))
        self.assertContains(response, "Training day")

    def test_no_goal_means_no_suggestion_card(self):
        response = self.client.get(reverse("nutrition:dashboard"))
        self.assertIsNone(response.context["suggestion"])

    def test_a_goal_with_no_weight_history_shows_insufficient_data(self):
        goal = services.set_goal(
            self.alice, goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        breakdown = macros.calculate_macros(Decimal("80"), 2100, GoalType.FAT_LOSS_MODERATE)
        services.set_target(
            self.alice, goal=goal, daily_calories=2100, macro_breakdown=breakdown,
            source=TargetSource.CALCULATED, reason="",
        )
        response = self.client.get(reverse("nutrition:dashboard"))
        self.assertEqual(
            response.context["suggestion"].action, suggestions.AdjustmentAction.INSUFFICIENT_DATA
        )

    def test_weight_chart_is_none_with_fewer_than_two_readings(self):
        response = self.client.get(reverse("nutrition:dashboard"))
        self.assertIsNone(response.context["weight_chart"])

    def test_weight_chart_appears_with_enough_readings(self):
        _log_weight(self.alice, 0, "80.0")
        _log_weight(self.alice, 5, "79.5")
        response = self.client.get(reverse("nutrition:dashboard"))
        self.assertIsNotNone(response.context["weight_chart"])


class AcceptAdjustmentSuggestionViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        self.goal = services.set_goal(
            self.alice, goal_type=GoalType.FAT_LOSS_MODERATE,
            target_rate_kg_per_week=Decimal("-0.5"),
        )
        breakdown = macros.calculate_macros(Decimal("80"), 2100, GoalType.FAT_LOSS_MODERATE)
        self.target = services.set_target(
            self.alice, goal=self.goal, daily_calories=2100, macro_breakdown=breakdown,
            source=TargetSource.CALCULATED, reason="",
        )

    def test_accepting_when_off_track_creates_a_new_target(self):
        for offset, weight in [(0, "80.0"), (10, "79.8"), (20, "79.6"), (30, "79.4")]:
            _log_weight(self.alice, offset, weight)
        self.client.post(reverse("nutrition:accept-adjustment-suggestion"))
        self.target.refresh_from_db()
        self.assertIsNotNone(self.target.ended_at)
        new_target = NutritionTarget.objects.get(user=self.alice, ended_at__isnull=True)
        self.assertEqual(new_target.source, TargetSource.ADJUSTED)

    def test_accepting_with_insufficient_data_does_nothing(self):
        self.client.post(reverse("nutrition:accept-adjustment-suggestion"))
        self.target.refresh_from_db()
        self.assertIsNone(self.target.ended_at)

    def test_requires_post(self):
        response = self.client.get(reverse("nutrition:accept-adjustment-suggestion"))
        self.assertEqual(response.status_code, 405)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.post(reverse("nutrition:accept-adjustment-suggestion"))
        self.assertEqual(response.status_code, 302)


class EstimateBodyFatPercentTests(TestCase):
    def test_a_bigger_waist_relative_to_neck_means_a_higher_estimate(self):
        lean = calculators.estimate_body_fat_percent(
            biological_sex=BiologicalSex.MALE,
            height_cm=Decimal("180"),
            neck_cm=Decimal("38"),
            waist_cm=Decimal("80"),
        )
        heavier = calculators.estimate_body_fat_percent(
            biological_sex=BiologicalSex.MALE,
            height_cm=Decimal("180"),
            neck_cm=Decimal("38"),
            waist_cm=Decimal("100"),
        )
        self.assertLess(lean, heavier)

    def test_male_result_is_in_a_plausible_range_for_realistic_measurements(self):
        result = calculators.estimate_body_fat_percent(
            biological_sex=BiologicalSex.MALE,
            height_cm=Decimal("180"),
            neck_cm=Decimal("38"),
            waist_cm=Decimal("90"),
        )
        self.assertTrue(Decimal("10") < result < Decimal("30"))

    def test_female_result_needs_hip_and_is_in_a_plausible_range(self):
        result = calculators.estimate_body_fat_percent(
            biological_sex=BiologicalSex.FEMALE,
            height_cm=Decimal("165"),
            neck_cm=Decimal("32"),
            waist_cm=Decimal("75"),
            hip_cm=Decimal("100"),
        )
        self.assertTrue(Decimal("10") < result < Decimal("40"))

    def test_female_without_hip_returns_none_rather_than_crashing(self):
        result = calculators.estimate_body_fat_percent(
            biological_sex=BiologicalSex.FEMALE,
            height_cm=Decimal("165"),
            neck_cm=Decimal("32"),
            waist_cm=Decimal("75"),
        )
        self.assertIsNone(result)

    def test_a_waist_not_bigger_than_the_neck_returns_none_rather_than_a_nonsense_negative(self):
        result = calculators.estimate_body_fat_percent(
            biological_sex=BiologicalSex.MALE,
            height_cm=Decimal("180"),
            neck_cm=Decimal("40"),
            waist_cm=Decimal("35"),
        )
        self.assertIsNone(result)


class EstimateDailyWaterLitersTests(TestCase):
    """Plain Decimal arithmetic — exact, hand-derivable expected values."""

    def test_sedentary_80kg_is_exactly_the_base_rate(self):
        result = calculators.estimate_daily_water_liters(Decimal("80"), ActivityLevel.SEDENTARY)
        self.assertEqual(result, Decimal("2.6"))  # 80 * 33ml = 2640ml, +0 bonus

    def test_moderate_80kg_adds_the_moderate_bonus(self):
        result = calculators.estimate_daily_water_liters(Decimal("80"), ActivityLevel.MODERATE)
        self.assertEqual(result, Decimal("3.1"))  # 2640ml + 500ml = 3140ml

    def test_very_active_80kg_adds_the_largest_bonus(self):
        result = calculators.estimate_daily_water_liters(
            Decimal("80"), ActivityLevel.VERY_ACTIVE
        )
        self.assertEqual(result, Decimal("3.6"))  # 2640ml + 1000ml = 3640ml


class CalculatorsHomeViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_renders_without_a_nutrition_profile(self):
        # The whole point of the calculators is that they don't require
        # onboarding — unlike NutritionDashboardView, this must not
        # redirect an un-onboarded user into the wizard.
        self.assertFalse(hasattr(self.alice, "nutrition_profile"))
        response = self.client.get(reverse("nutrition:calculators-home"))
        self.assertEqual(response.status_code, 200)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:calculators-home"))
        self.assertEqual(response.status_code, 302)


class BmrTdeeCalculatorViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_blank_get_shows_the_form_with_no_result(self):
        response = self.client.get(reverse("nutrition:calculator-bmr-tdee"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["result"])

    def test_a_complete_submission_matches_calling_energy_directly(self):
        response = self.client.get(
            reverse("nutrition:calculator-bmr-tdee"),
            {
                "biological_sex": BiologicalSex.MALE,
                "age_years": 30,
                "height_cm": "180",
                "weight_kg": "80",
                "activity_level": ActivityLevel.MODERATE,
            },
        )
        expected_bmr = energy.calculate_bmr(Decimal("80"), Decimal("180"), 30, BiologicalSex.MALE)
        expected_tdee = energy.calculate_tdee(expected_bmr, ActivityLevel.MODERATE)
        self.assertEqual(response.context["result"]["bmr"], expected_bmr)
        self.assertEqual(response.context["result"]["tdee"], expected_tdee)

    def test_imperial_units_are_converted_before_reaching_the_energy_module(self):
        self.alice.unit_system = "imperial"
        self.alice.save(update_fields=["unit_system"])
        response = self.client.get(
            reverse("nutrition:calculator-bmr-tdee"),
            {
                "biological_sex": BiologicalSex.MALE,
                "age_years": 30,
                "height_cm": "70.9",  # ~180cm in inches
                "weight_kg": "176.4",  # ~80kg in lb
                "activity_level": ActivityLevel.SEDENTARY,
            },
        )
        # Close to (not exactly equal to, thanks to rounding round-trips)
        # the metric result for the same real body.
        expected_bmr = energy.calculate_bmr(Decimal("80"), Decimal("180"), 30, BiologicalSex.MALE)
        self.assertAlmostEqual(
            int(response.context["result"]["bmr"]), int(expected_bmr), delta=5
        )

    def test_invalid_input_shows_errors_instead_of_crashing(self):
        response = self.client.get(
            reverse("nutrition:calculator-bmr-tdee"),
            {
                "biological_sex": BiologicalSex.MALE,
                "age_years": "not a number",
                "height_cm": "180",
                "weight_kg": "80",
                "activity_level": ActivityLevel.MODERATE,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["result"])
        self.assertTrue(response.context["form"].errors)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:calculator-bmr-tdee"))
        self.assertEqual(response.status_code, 302)


class MacroCalculatorViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_a_complete_submission_matches_calling_macros_directly(self):
        response = self.client.get(
            reverse("nutrition:calculator-macros"),
            {
                "weight_kg": "80",
                "daily_calories": 2100,
                "goal_type": GoalType.FAT_LOSS_MODERATE,
            },
        )
        expected = macros.calculate_macros(Decimal("80"), 2100, GoalType.FAT_LOSS_MODERATE)
        self.assertEqual(response.context["result"].protein_grams, expected.protein_grams)
        self.assertEqual(response.context["result"].fat_grams, expected.fat_grams)
        self.assertEqual(
            response.context["result"].carbohydrate_grams, expected.carbohydrate_grams
        )

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:calculator-macros"))
        self.assertEqual(response.status_code, 302)


class BodyFatCalculatorViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_a_complete_male_submission_shows_a_result(self):
        response = self.client.get(
            reverse("nutrition:calculator-body-fat"),
            {
                "biological_sex": BiologicalSex.MALE,
                "height_cm": "180",
                "neck_cm": "38",
                "waist_cm": "90",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["result"])

    def test_female_without_hip_shows_a_validation_error_not_a_crash(self):
        response = self.client.get(
            reverse("nutrition:calculator-body-fat"),
            {
                "biological_sex": BiologicalSex.FEMALE,
                "height_cm": "165",
                "neck_cm": "32",
                "waist_cm": "75",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["result"])
        self.assertIn("hip_cm", response.context["form"].errors)

    def test_a_nonsensical_waist_shows_the_empty_state_not_a_crash(self):
        response = self.client.get(
            reverse("nutrition:calculator-body-fat"),
            {
                "biological_sex": BiologicalSex.MALE,
                "height_cm": "180",
                "neck_cm": "40",
                "waist_cm": "35",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["result"])

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:calculator-body-fat"))
        self.assertEqual(response.status_code, 302)


class WaterIntakeCalculatorViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_a_complete_submission_matches_calling_calculators_directly(self):
        response = self.client.get(
            reverse("nutrition:calculator-water-intake"),
            {"weight_kg": "80", "activity_level": ActivityLevel.MODERATE},
        )
        expected = calculators.estimate_daily_water_liters(Decimal("80"), ActivityLevel.MODERATE)
        self.assertEqual(response.context["result"], expected)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("nutrition:calculator-water-intake"))
        self.assertEqual(response.status_code, 302)


class DateInputWidgetLocaleFormatTests(TestCase):
    """Regression, found live: every `type="date"` widget in this app
    rendered its value in the *active locale's* date format (e.g.
    Finnish "17.08.2026") rather than the ISO 8601 format
    (`YYYY-MM-DD`) HTML5 `<input type="date">` requires for its
    `value` attribute — a browser silently rejects any other format
    and shows the picker empty instead. `format="%Y-%m-%d"` on the
    widget itself pins the display format regardless of locale, the
    same fix already established in apps.activities.forms's own
    `date` widget. Verified with Finnish active specifically, since
    the bug is invisible with English active (Django's default
    locale format already happens to be ISO-like there)."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_log_recipe_forms_date_field_renders_iso_format_in_finnish(self):
        with translation_override("fi"):
            form = LogRecipeForm(user=self.alice)
            rendered = str(form["date"])
        self.assertIn(f'value="{timezone.localdate().isoformat()}"', rendered)
        self.assertNotIn(".", rendered.split('value="')[1].split('"')[0])

    def test_log_diet_plan_forms_date_field_renders_iso_format_in_finnish(self):
        with translation_override("fi"):
            form = LogDietPlanForm(initial={"date": date(2026, 3, 7)})
            rendered = str(form["date"])
        self.assertIn('value="2026-03-07"', rendered)

    def test_body_step_forms_birth_date_field_renders_iso_format_in_finnish(self):
        with translation_override("fi"):
            form = BodyStepForm(user=self.alice, initial={"birth_date": date(1990, 12, 25)})
            rendered = str(form["birth_date"])
        self.assertIn('value="1990-12-25"', rendered)


class NumberInputLocaleFormatTests(TestCase):
    """Same class of bug as DateInputWidgetLocaleFormatTests above, for
    HTML5 `type="number"` instead of `type="date"`: its `value`
    attribute must use a period decimal separator regardless of
    locale (the HTML spec's floating-point number token), but a raw
    `{{ some_decimal }}` renders with the active locale's decimal
    separator (Finnish uses a comma) — a browser silently rejects the
    malformed value and leaves the field empty instead of prefilled.
    Found live in a Finnish session while verifying the "Most used"
    quick-add panel. Fixed with `{% load l10n %}` + `|unlocalize` on
    every hand-written `type="number"` value in
    `_food_search_results.html`/`_most_used_foods.html` — the only
    hand-written number inputs in the app; every Django-form-rendered
    NumberInput widget elsewhere already avoids this on its own."""

    def setUp(self):
        # A user's active UI language comes from their stored
        # User.language preference (apps.accounts.middleware.
        # UserLanguageMiddleware), re-derived fresh on every request —
        # translation.override() wrapped around self.client.get() gets
        # overridden right back by that middleware, so the language has
        # to be set here instead (see this class's own docstring).
        self.alice = User.objects.create_user(
            username="alice", password="s3cret-pass", language="fi"
        )
        self.client.login(username="alice", password="s3cret-pass")
        self.food = make_food(self.alice, serving_size=Decimal("100.5"))

    def test_search_results_quantity_field_uses_a_period_in_finnish(self):
        with mock.patch.object(openfoodfacts, "search_products", return_value=[]):
            response = self.client.get(
                reverse("nutrition:food-search"), {"q": self.food.name, "mode": "diary"}
            )
        # serving_size is a decimal_places=2 field, so "100.5" in is
        # "100.50" back out — the point being the period, not the
        # trailing zero.
        self.assertContains(response, 'value="100.50"')
        self.assertNotContains(response, 'value="100,50"')

    def test_most_used_quantity_field_uses_a_period_in_finnish(self):
        breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=breakfast,
            food=self.food, quantity=Decimal("150.5"),
        )
        response = self.client.get(reverse("nutrition:diary-add-entry"))
        self.assertContains(response, 'value="150.50"')
        self.assertNotContains(response, 'value="150,50"')
