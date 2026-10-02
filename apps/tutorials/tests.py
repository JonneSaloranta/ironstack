"""The tours can't silently break or go stale (docs/TUTORIALS.md):

- every required step's `data-tour` anchor is on its page,
- every page without URL arguments has a tour or a NO_TOUR_NEEDED entry,
- every tour text is translated into every language the app ships.
"""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import translation

from apps.nutrition.models import DietPlan, Food, MealSlot, Recipe, RecipeIngredient
from apps.programs.models import Program
from apps.programs.services import copy_program
from apps.stretching import services as stretching_services
from apps.stretching.models import StretchRoutine
from apps.workouts import services as workout_services

from . import services
from .models import TutorialCompletion
from .tours import NO_TOUR_NEEDED, TOURS

User = get_user_model()

# Strings that are genuinely spelled the same in some language
# (checked by hand) — keep this short; anything else must differ.
SAME_IN_TRANSLATION = {
    ("Home", "it"),
    ("Stretching", "sv"), ("Stretching", "it"),
}


def make_rich_user(username="tourist"):
    """A user with something on every page, so a tour's required
    anchors have their content to sit on."""
    user = User.objects.create_user(username=username, password="s3cret-pass")
    user.onboarding_completed = True
    user.language_chosen = True
    user.nutrition_enabled = True
    user.stretching_enabled = True
    user.save()

    template = Program.objects.filter(owner__isnull=True).first()
    program = copy_program(template, user)
    workout = program.workouts.first()
    workout_services.start_session(user, workout)

    food = Food.objects.create(
        owner=user, name="Oats", serving_size=100, serving_unit="g", calories=380,
        protein_grams=Decimal("13"), carbohydrate_grams=Decimal("60"), fat_grams=Decimal("7"),
    )
    recipe = Recipe.objects.create(owner=user, name="Porridge", servings=1)
    RecipeIngredient.objects.create(recipe=recipe, food=food, quantity=Decimal("80"))
    DietPlan.objects.create(
        user=user, name="Plan", target_calories=2000, target_protein_grams=Decimal("150"),
        target_carbohydrate_grams=Decimal("200"), target_fat_grams=Decimal("60"),
    )
    from apps.nutrition.models import DiaryEntry

    DiaryEntry.objects.create(
        user=user, date=date.today(), meal_slot=MealSlot.objects.filter(owner=None).first(),
        food=food, quantity=Decimal("80"),
    )
    from apps.nutrition.models import NutritionProfile

    NutritionProfile.objects.create(
        user=user, biological_sex="male", birth_date=date(1990, 1, 1),
        activity_job="sedentary", activity_level="moderate",
    )
    routine = StretchRoutine.objects.filter(owner__isnull=True, active=True).first()
    stretching_services.start_session(user, routine=routine)
    return user


class TourRegistryTests(TestCase):
    def test_keys_are_unique(self):
        keys = [tour.key for tour in TOURS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_a_page_has_at_most_one_tour(self):
        names = [name for tour in TOURS for name in tour.view_names]
        self.assertEqual(len(names), len(set(names)))

    def test_sections_are_kept_together(self):
        """profile → Tutorials groups consecutive tours by section."""
        seen, previous = set(), None
        for tour in TOURS:
            if tour.section != previous:
                self.assertNotIn(str(tour.section), seen, f"{tour.key}: section split up")
                seen.add(str(tour.section))
                previous = tour.section

    def test_every_tour_has_a_required_step(self):
        """A tour made only of optional steps could quietly show nothing."""
        for tour in TOURS:
            with self.subTest(tour=tour.key):
                self.assertTrue(any(not step.optional for step in tour.steps))

    def test_no_tour_needed_names_real_pages_without_a_tour(self):
        all_names = set(_url_names())
        toured = set(services.tours_by_view_name())
        for name in NO_TOUR_NEEDED:
            with self.subTest(name=name):
                self.assertIn(name, all_names, "stale NO_TOUR_NEEDED entry")
                self.assertNotIn(name, toured, "has a tour and a NO_TOUR_NEEDED entry")

    def test_every_tour_text_is_translated(self):
        languages = [code for code, _name in settings.LANGUAGES if code != "en"]
        texts = set()
        for tour in TOURS:
            texts.add(tour.title)
            texts.add(tour.section)
            for step in tour.steps:
                texts.update([step.title, step.body])
        for text in texts:
            with translation.override("en"):
                english = str(text)
            for code in languages:
                if (english, code) in SAME_IN_TRANSLATION:
                    continue
                with translation.override(code), self.subTest(text=english, language=code):
                    self.assertNotEqual(str(text), english, "missing translation")


def _url_names(resolver=None, namespace=""):
    resolver = resolver or get_resolver()
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            ns = f"{namespace}{pattern.namespace}:" if pattern.namespace else namespace
            yield from _url_names(pattern, ns)
        elif isinstance(pattern, URLPattern) and pattern.name:
            yield namespace + pattern.name


def _argless_url_names():
    for name in sorted(set(_url_names())):
        try:
            yield name, reverse(name)
        except Exception:  # needs arguments
            continue


class TourAnchorTests(TestCase):
    """Renders each tour's page and checks its required anchors."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_rich_user()

    def setUp(self):
        self.client.force_login(self.user)

    def test_every_required_anchor_is_on_its_page(self):
        for tour in TOURS:
            url = tour.url_for(self.user)
            with self.subTest(tour=tour.key):
                self.assertIsNotNone(url, "no page to render this tour on")
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)
                html = response.content.decode()
                for step in tour.steps:
                    if step.anchor and not step.optional:
                        self.assertIn(
                            f'data-tour="{step.anchor}"', html,
                            f"{tour.key}: required anchor {step.anchor!r} missing on {url}",
                        )

    def test_every_page_has_a_tour_or_a_reason(self):
        """A new page must either get a tour or be added to NO_TOUR_NEEDED."""
        import tempfile
        from pathlib import Path
        from unittest import mock

        from apps.core import backups

        # The staff-only backup page reads BACKUP_DIR (/app/backups, only
        # present inside the container) — point it at an empty temp dir.
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patcher = mock.patch.object(backups, "BACKUP_DIR", Path(tmpdir.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user.is_staff = True
        self.user.save()
        toured = services.tours_by_view_name()
        for name, url in _argless_url_names():
            if name.startswith(("admin:", "api:", "oidc", "rest_framework")):
                continue
            response = self.client.get(url)
            if response.status_code != 200 or 'id="main-content"' not in response.content.decode():
                continue
            with self.subTest(page=name):
                self.assertTrue(
                    name in toured or name in NO_TOUR_NEEDED,
                    f"{name} ({url}) has no tour — add one to apps/tutorials/tours.py or "
                    "list it in NO_TOUR_NEEDED with a reason",
                )


class TourBehaviourTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="newbie", password="s3cret-pass")
        self.user.onboarding_completed = True
        self.user.language_chosen = True
        self.user.save()
        self.client.force_login(self.user)

    def _tour_on_dashboard(self, **params):
        return self.client.get(reverse("dashboard"), params).context.get("tutorial_data")

    def test_starts_by_itself_the_first_time(self):
        data = self._tour_on_dashboard()
        self.assertEqual(data["key"], "dashboard")
        self.assertEqual(data["steps"][0]["title"], "Welcome to IronStack")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'id="tutorial-data"')
        self.assertContains(response, "js/tutorial.js")

    def test_not_again_once_completed(self):
        self.client.post(reverse("tutorials:complete", args=["dashboard"]))
        completions = TutorialCompletion.objects.filter(user=self.user, tour="dashboard")
        self.assertTrue(completions.exists())
        self.assertIsNone(self._tour_on_dashboard())

    def test_not_while_onboarding_is_still_open(self):
        self.user.onboarding_completed = False
        self.user.save()
        self.assertIsNone(self._tour_on_dashboard())

    def test_dont_show_tutorials_turns_them_all_off(self):
        self.client.post(reverse("tutorials:disable"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.tutorials_enabled)
        self.assertIsNone(self._tour_on_dashboard())

    def test_start_button_runs_it_even_when_seen_or_off(self):
        self.user.tutorials_enabled = False
        self.user.save()
        services.mark_completed(self.user, "dashboard")
        self.assertEqual(self._tour_on_dashboard(tour="dashboard")["key"], "dashboard")

    def test_show_again_re_arms_a_tour(self):
        services.mark_completed(self.user, "dashboard")
        self.client.post(reverse("tutorials:reset", args=["dashboard"]))
        self.assertEqual(self._tour_on_dashboard()["key"], "dashboard")

    def test_show_all_again(self):
        services.mark_completed(self.user, "dashboard")
        services.mark_completed(self.user, "profile")
        self.client.post(reverse("tutorials:reset-all"))
        self.assertFalse(TutorialCompletion.objects.filter(user=self.user).exists())

    def test_settings_toggle(self):
        self.client.post(reverse("tutorials:settings"), {})
        self.user.refresh_from_db()
        self.assertFalse(self.user.tutorials_enabled)
        self.client.post(reverse("tutorials:settings"), {"enabled": "on"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.tutorials_enabled)

    def test_unknown_tour_is_a_404(self):
        response = self.client.post(reverse("tutorials:complete", args=["no-such-tour"]))
        self.assertEqual(response.status_code, 404)

    def test_completing_needs_a_post(self):
        response = self.client.get(reverse("tutorials:complete", args=["dashboard"]))
        self.assertEqual(response.status_code, 405)

    def test_tutorials_page_lists_every_tour_with_its_state(self):
        services.mark_completed(self.user, "dashboard")
        response = self.client.get(reverse("tutorials:list"))
        self.assertContains(response, "Seen")
        self.assertContains(response, "Food diary")
        self.assertContains(response, f'{reverse("dashboard")}?tour=dashboard')

    def test_profile_links_to_the_tutorials_page(self):
        response = self.client.get(reverse("profile"))
        self.assertContains(response, reverse("tutorials:list"))

    def test_other_users_progress_is_separate(self):
        other = User.objects.create_user(username="other", password="s3cret-pass")
        services.mark_completed(other, "dashboard")
        self.assertEqual(self._tour_on_dashboard()["key"], "dashboard")
