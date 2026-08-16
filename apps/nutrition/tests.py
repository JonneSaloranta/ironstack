from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.measurements.models import BodyMeasurement, MeasurementType
from apps.nutrition import energy, macros, openfoodfacts, services, suggestions, trends

from .models import (
    ActivityJob,
    ActivityLevel,
    BiologicalSex,
    DiaryEntry,
    DietPlan,
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
