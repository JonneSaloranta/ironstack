"""Layout holds up with real-world content (the `accessibility` marker —
a real Chromium, requirements/a11y.txt; see test_ui_behaviour.py).

Every page whose content varies — names, brands, long Finnish compound
words — is rendered at a 360px phone width with deliberately long
content next to short content, and checked for: anything running past
the screen or its card, and rows in one list whose actions don't line
up (a long name pushing its button under the text while its
neighbours keep theirs on the right). docs/UI_COMPONENTS.md "Cards and
rows" describes the rules these enforce.
"""

import datetime
import os
from pathlib import Path

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytest.importorskip("playwright", reason="requirements/a11y.txt not installed")

from decimal import Decimal  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from apps.activities.models import ActivityType  # noqa: E402
from apps.coaching import services as coaching  # noqa: E402
from apps.exercises.models import Exercise  # noqa: E402
from apps.measurements.models import MeasurementType  # noqa: E402
from apps.nutrition import services as nutrition  # noqa: E402
from apps.nutrition.models import (  # noqa: E402
    DiaryEntry,
    DietPlan,
    DietPlanMeal,
    Food,
    MealSlot,
    NutritionProfile,
    Recipe,
    RecipeIngredient,
)
from apps.programs.models import Program, Workout  # noqa: E402
from apps.stretching.models import StretchRoutine  # noqa: E402
from apps.workouts import services as workout_services  # noqa: E402

User = get_user_model()

LONG = "Kananrintafilee hunaja-soijamarinadissa, paahdetut juurekset ja yrttinen kastike"
WORD = "Lihaliemikuutiokeittopohjavalmistepakkaus"

# Measured in the page itself (apps/core/layout_check.js).
CHECK_LAYOUT = (Path(__file__).parent / "layout_check.js").read_text()


@pytest.mark.accessibility
class LongContentLayoutTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username="maximiliana-kristiina-with-a-long-name", password="s3cret-pass",
            first_name="Maximiliana", onboarding_completed=True, language="fi",
            language_chosen=True, tutorials_enabled=False, is_personal_trainer=True,
        )
        self.urls = self._seed()
        self.page = self.browser.new_page(viewport={"width": 360, "height": 800})
        self.page.set_default_timeout(10000)
        self.addCleanup(self.page.close)
        self.page.goto(f"{self.live_server_url}/accounts/login/")
        self.page.fill("input[name=username]", self.user.username)
        self.page.fill("input[name=password]", "s3cret-pass")
        self.page.click("button[type=submit]")
        self.page.wait_for_load_state("networkidle")

    def _seed(self):
        user = self.user
        NutritionProfile.objects.create(
            user=user, biological_sex="female", birth_date=datetime.date(1990, 1, 1),
            activity_job="sedentary", activity_level="moderate",
        )
        foods = [
            Food.objects.create(
                owner=user, name=name, brand="Valion Erikoisjalosteet Oy", serving_size=100,
                serving_unit="g", calories=250, protein_grams=Decimal("12.5"),
                carbohydrate_grams=Decimal("30"), fat_grams=Decimal("8.25"),
            )
            for name in ("Oats", LONG, WORD)
        ]
        # Everything is created here rather than taken from the
        # migrations' seed data: a live-server test empties the database
        # afterwards, so seed rows aren't there on a re-run (--reuse-db).
        slots = [
            MealSlot.objects.get_or_create(name=name, owner=None, defaults={"order": order})[0]
            for order, name in enumerate(("Breakfast", "Lunch", "Dinner"))
        ]
        recipes = []
        for name in ("Puuro", LONG, WORD):
            recipe = Recipe.objects.create(owner=user, name=name, servings=2)
            RecipeIngredient.objects.create(recipe=recipe, food=foods[1], quantity=Decimal("150"))
            recipe.meal_slots.set(slots)
            recipes.append(recipe)
        today = timezone.localdate()
        for food in foods:
            DiaryEntry.objects.create(
                user=user, date=today, meal_slot=slots[0], food=food, quantity=Decimal("123.45")
            )
        nutrition.create_quick_diary_entry(
            user, target_date=today, meal_slot=slots[0], name=LONG, calories=950
        )
        plan = DietPlan.objects.create(
            user=user, name=LONG, target_calories=2200, target_protein_grams=150,
            target_carbohydrate_grams=220, target_fat_grams=70,
        )
        meal = DietPlanMeal.objects.create(diet_plan=plan, meal_slot=slots[0], target_calories=700)
        for food in foods:
            meal.items.create(food=food, quantity=Decimal("100"))
        program = Program.objects.create(owner=user, name=LONG, description=LONG)
        Workout.objects.create(program=program, name=LONG)
        exercise = Exercise.objects.create(owner=user, name=LONG)
        session = workout_services.start_session(user, program.workouts.first())
        workout_services.add_performed_exercise(session, exercise)
        MeasurementType.objects.create(owner=user, name=WORD, unit_kind="length")
        ActivityType.objects.create(owner=user, name=WORD)
        routine = StretchRoutine.objects.create(owner=user, name=LONG)
        for username in ("aino", "ville-valtteri-kristian-oikein-pitka-kayttajanimi"):
            client = User.objects.create_user(username=username, password="s3cret-pass")
            request = coaching.send_coaching_request(client, user)
            coaching.accept_coaching_request(request, acting_user=user)
        return [
            reverse("dashboard"),
            reverse("nutrition:dashboard"),
            reverse("nutrition:diary-day"),
            reverse("nutrition:diary-add-entry"),
            reverse("nutrition:food-list"),
            reverse("nutrition:food-detail", args=[foods[2].pk]),
            reverse("nutrition:recipe-list"),
            reverse("nutrition:recipe-detail", args=[recipes[1].pk]),
            reverse("nutrition:recipe-ingredient-create", args=[recipes[0].pk]),
            reverse("nutrition:diet-plan-list"),
            reverse("nutrition:diet-plan-detail", args=[plan.pk]),
            reverse("nutrition:diet-plan-share", args=[plan.pk]),
            reverse("nutrition:diet-plan-shopping", args=[plan.pk]),
            reverse("nutrition:calculators-home"),
            reverse("nutrition:stats"),
            reverse("programs:program-list"),
            reverse("programs:program-detail", args=[program.pk]),
            reverse("programs:program-update", args=[program.pk]),
            reverse("workouts:session-list"),
            reverse("workouts:session-detail", args=[session.pk]),
            reverse("workouts:session-train", args=[session.pk]),
            reverse("exercises:exercise-list"),
            reverse("exercises:exercise-detail", args=[exercise.pk]),
            reverse("measurements:type-list"),
            reverse("activities:type-list"),
            reverse("stretching:home"),
            reverse("stretching:routine-detail", args=[routine.pk]),
            reverse("coaching:client-list"),
            reverse("profile"),
            reverse("tutorials:list"),
        ]

    def test_long_content_stays_inside_and_rows_line_up(self):
        problems = []
        for url in self.urls:
            self.page.goto(f"{self.live_server_url}{url}")
            self.page.wait_for_load_state("networkidle")
            for kind, detail in self.page.evaluate(CHECK_LAYOUT):
                problems.append(f"{url}: {kind} {detail}")
        self.assertEqual(problems, [], "\n" + "\n".join(dict.fromkeys(problems)))
