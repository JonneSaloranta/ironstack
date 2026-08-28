from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.exercises.models import Exercise
from apps.workouts import services as workout_services

from . import crypto, services
from .models import ApiContext, ApiKey, ApiKeyPermission, ApiSettings, RateLimitTier

User = get_user_model()


def _all_permissions(**overrides):
    """Every context granted every CRUD verb by default, with per-context
    overrides for tests that need a narrower key."""
    contexts = [
        "profile",
        "exercises",
        "programs",
        "workouts",
        "measurements",
        "activities",
        "records",
        "analytics",
        "nutrition",
    ]
    base = {
        context: {"can_create": True, "can_read": True, "can_update": True, "can_delete": True}
        for context in contexts
    }
    base.update(overrides)
    return base


def _create_key(user, **permission_overrides):
    api_key, raw_secret = services.create_api_key(
        user, name="Test key", permissions=_all_permissions(**permission_overrides)
    )
    return api_key, raw_secret


class RateLimitTierModelTests(TestCase):
    def test_default_tiers_are_seeded(self):
        """apps.api migration 0002 — a fresh install has something to
        assign new keys to immediately."""
        self.assertTrue(RateLimitTier.objects.filter(is_default=True).exists())

    def test_str_shows_the_rates(self):
        tier = RateLimitTier.objects.create(
            name="Custom", requests_per_minute=42, requests_per_day=999
        )
        self.assertIn("42", str(tier))
        self.assertIn("999", str(tier))


class ApiSettingsModelTests(TestCase):
    def test_load_creates_a_singleton_with_sensible_defaults(self):
        settings_obj = ApiSettings.load()
        self.assertEqual(settings_obj.pk, 1)
        self.assertEqual(settings_obj.max_api_keys_per_user, 10)

    def test_load_always_returns_the_same_row(self):
        first = ApiSettings.load()
        first.max_api_keys_per_user = 3
        first.save()
        second = ApiSettings.load()
        self.assertEqual(second.pk, 1)
        self.assertEqual(second.max_api_keys_per_user, 3)

    def test_save_always_pins_pk_to_1(self):
        obj = ApiSettings(max_api_keys_per_user=5)
        obj.save()
        self.assertEqual(obj.pk, 1)
        self.assertEqual(ApiSettings.objects.count(), 1)


class CryptoTests(TestCase):
    def test_generate_secret_is_unique_each_time(self):
        first = crypto.generate_secret()
        second = crypto.generate_secret()
        self.assertNotEqual(first[0], second[0])

    def test_hash_is_deterministic(self):
        raw_secret, _prefix, key_hash = crypto.generate_secret()
        self.assertEqual(crypto.hash_secret(raw_secret), key_hash)

    def test_prefix_is_a_stable_slice_of_the_secret(self):
        raw_secret, prefix, _hash = crypto.generate_secret()
        self.assertTrue(raw_secret.startswith(prefix))
        self.assertTrue(prefix.startswith("isk_"))


class ApiKeyServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_create_api_key_returns_the_raw_secret_once(self):
        api_key, raw_secret = _create_key(self.alice)
        self.assertEqual(crypto.hash_secret(raw_secret), api_key.key_hash)

    def test_create_api_key_assigns_the_default_tier(self):
        api_key, _secret = _create_key(self.alice)
        self.assertEqual(api_key.tier, RateLimitTier.objects.get(is_default=True))

    def test_create_api_key_creates_all_context_permission_rows(self):
        api_key, _secret = _create_key(self.alice)
        self.assertEqual(api_key.permissions.count(), len(ApiContext.choices))

    def test_permissions_reflect_what_was_requested(self):
        api_key, _secret = _create_key(self.alice, exercises={"can_read": True})
        exercises_perm = api_key.permissions.get(context="exercises")
        self.assertTrue(exercises_perm.can_read)
        self.assertFalse(exercises_perm.can_create)

    def test_remaining_quota_decreases_as_keys_are_created(self):
        self.assertEqual(services.remaining_key_quota(self.alice), 10)
        _create_key(self.alice)
        self.assertEqual(services.remaining_key_quota(self.alice), 9)

    def test_create_api_key_raises_once_quota_is_exhausted(self):
        ApiSettings.load()  # ensure the singleton exists
        settings_obj = ApiSettings.load()
        settings_obj.max_api_keys_per_user = 1
        settings_obj.save()
        _create_key(self.alice)
        with self.assertRaises(ValueError):
            _create_key(self.alice)

    def test_quota_is_scoped_per_user(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _create_key(self.alice)
        self.assertEqual(services.remaining_key_quota(bob), 10)

    def test_revoke_deletes_the_key(self):
        api_key, _secret = _create_key(self.alice)
        services.revoke_api_key(api_key)
        self.assertFalse(ApiKey.objects.filter(pk=api_key.pk).exists())

    def test_set_permissions_updates_an_existing_key_without_duplicating_rows(self):
        api_key, _secret = _create_key(self.alice)
        services.set_permissions(api_key, {"exercises": {"can_read": True}})
        self.assertEqual(api_key.permissions.count(), len(ApiContext.choices))
        self.assertTrue(api_key.permissions.get(context="exercises").can_read)


class ApiKeyAuthenticationTests(APITestCase):
    """apps.api.auth.ApiKeyAuthentication — exercised through a real view
    (the profile endpoint) rather than unit-testing the authenticator in
    isolation, since its whole job is only meaningful in that context."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.api_key, self.raw_secret = _create_key(self.alice)

    def test_a_valid_key_authenticates(self):
        response = self.client.get(
            reverse("api:profile"), HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "alice")

    def test_no_authorization_header_is_rejected(self):
        response = self.client.get(reverse("api:profile"))
        self.assertEqual(response.status_code, 401)

    def test_an_unknown_secret_is_rejected(self):
        response = self.client.get(
            reverse("api:profile"), HTTP_AUTHORIZATION="Bearer isk_not-a-real-key"
        )
        self.assertEqual(response.status_code, 401)

    def test_a_revoked_key_is_rejected(self):
        services.revoke_api_key(self.api_key)
        response = self.client.get(
            reverse("api:profile"), HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        )
        self.assertEqual(response.status_code, 401)

    def test_an_inactive_key_is_rejected(self):
        self.api_key.is_active = False
        self.api_key.save()
        response = self.client.get(
            reverse("api:profile"), HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        )
        self.assertEqual(response.status_code, 401)

    def test_a_successful_request_updates_last_used_at(self):
        self.assertIsNone(self.api_key.last_used_at)
        self.client.get(reverse("api:profile"), HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}")
        self.api_key.refresh_from_db()
        self.assertIsNotNone(self.api_key.last_used_at)


class ContextPermissionTests(APITestCase):
    """apps.api.permissions.HasContextPermission — exercised through the
    exercises endpoint (list = read, create = create)."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def _auth(self, raw_secret):
        return {"HTTP_AUTHORIZATION": f"Bearer {raw_secret}"}

    def test_a_key_with_read_can_list(self):
        _api_key, raw_secret = _create_key(self.alice, exercises={"can_read": True})
        response = self.client.get(reverse("api:exercise-list"), **self._auth(raw_secret))
        self.assertEqual(response.status_code, 200)

    def test_a_key_without_read_cannot_list(self):
        _api_key, raw_secret = _create_key(self.alice, exercises={"can_read": False})
        response = self.client.get(reverse("api:exercise-list"), **self._auth(raw_secret))
        self.assertEqual(response.status_code, 403)

    def test_a_key_with_read_but_not_create_cannot_post(self):
        _api_key, raw_secret = _create_key(
            self.alice, exercises={"can_read": True, "can_create": False}
        )
        response = self.client.post(
            reverse("api:exercise-list"),
            {"name": "Should Fail"},
            format="json",
            **self._auth(raw_secret),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Exercise.objects.filter(name="Should Fail").exists())

    def test_a_key_with_create_can_post(self):
        _api_key, raw_secret = _create_key(self.alice, exercises={"can_create": True})
        response = self.client.post(
            reverse("api:exercise-list"),
            {"name": "API Custom Curl"},
            format="json",
            **self._auth(raw_secret),
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Exercise.objects.filter(name="API Custom Curl", owner=self.alice).exists())

    def test_permissions_are_scoped_per_context_not_all_or_nothing(self):
        """A key granted only "programs" access must not be able to
        touch "exercises" — the whole point of context-scoped keys."""
        _api_key, raw_secret = _create_key(self.alice, exercises={}, programs={"can_read": True})
        exercises_response = self.client.get(
            reverse("api:exercise-list"), **self._auth(raw_secret)
        )
        programs_response = self.client.get(reverse("api:program-list"), **self._auth(raw_secret))
        self.assertEqual(exercises_response.status_code, 403)
        self.assertEqual(programs_response.status_code, 200)


class RateLimitingTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.tiny_tier = RateLimitTier.objects.create(
            name="TestTiny", requests_per_minute=2, requests_per_day=1000
        )
        api_key, self.raw_secret = _create_key(self.alice)
        api_key.tier = self.tiny_tier
        api_key.save()

    def test_requests_beyond_the_tiers_per_minute_limit_are_throttled(self):
        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}
        statuses = [
            self.client.get(reverse("api:profile"), **auth).status_code for _ in range(3)
        ]
        self.assertEqual(statuses, [200, 200, 429])

    def test_editing_a_tiers_rate_takes_effect_without_recreating_the_key(self):
        """The whole point of an admin-editable tier — bump the number
        and every key on it is affected immediately."""
        self.tiny_tier.requests_per_minute = 100
        self.tiny_tier.save()
        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}
        statuses = [
            self.client.get(reverse("api:profile"), **auth).status_code for _ in range(5)
        ]
        self.assertTrue(all(code == 200 for code in statuses))


class ProfileEndpointTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}

    def test_get_returns_the_authenticated_users_own_profile(self):
        response = self.client.get(reverse("api:profile"), **self._auth())
        self.assertEqual(response.data["username"], "alice")

    def test_patch_updates_preferences(self):
        response = self.client.patch(
            reverse("api:profile"), {"unit_system": "imperial"}, format="json", **self._auth()
        )
        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.unit_system, "imperial")

    def test_username_is_read_only(self):
        response = self.client.patch(
            reverse("api:profile"), {"username": "renamed"}, format="json", **self._auth()
        )
        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.username, "alice")


class WorkoutLoggingEndpointTests(APITestCase):
    """The most important end-to-end path: logging a set via the API
    must trigger PR detection exactly like apps.workouts.views.set_log
    does for the web UI — see apps.api.views.ExerciseSetViewSet."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="API Test Squat", owner=None)
        _api_key, self.raw_secret = _create_key(self.alice)

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}

    def test_starting_a_freeform_session_and_logging_a_set_creates_prs(self):
        session_response = self.client.post(
            reverse("api:session-list"), {}, format="json", **self._auth()
        )
        self.assertEqual(session_response.status_code, 201)
        session_id = session_response.data["id"]

        pe_response = self.client.post(
            reverse("api:performed-exercise-list"),
            {"session": session_id, "exercise": self.exercise.pk},
            format="json",
            **self._auth(),
        )
        self.assertEqual(pe_response.status_code, 201)
        pe_id = pe_response.data["id"]

        set_response = self.client.post(
            reverse("api:set-list"),
            {"performed_exercise": pe_id, "weight": "100.00", "reps": 5},
            format="json",
            **self._auth(),
        )
        self.assertEqual(set_response.status_code, 201)
        self.assertEqual(set_response.data["set_number"], 1)

        records_response = self.client.get(reverse("api:record-list"), **self._auth())
        self.assertGreater(records_response.data["count"], 0)

    def test_cannot_log_a_set_on_another_users_performed_exercise(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        bob_session = workout_services.start_session(bob, workout=None)
        bob_pe = workout_services.add_performed_exercise(bob_session, self.exercise)

        response = self.client.post(
            reverse("api:set-list"),
            {"performed_exercise": bob_pe.pk, "weight": "100.00", "reps": 5},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_log_a_set_once_the_session_is_completed(self):
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        workout_services.complete_session(session)

        response = self.client.post(
            reverse("api:set-list"),
            {"performed_exercise": performed.pk, "weight": "100.00", "reps": 5},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_completing_a_session_via_patch(self):
        session = workout_services.start_session(self.alice, workout=None)
        response = self.client.patch(
            reverse("api:session-detail", args=[session.pk]),
            {"status": "completed"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, "completed")
        self.assertIsNotNone(session.ended_at)

    def test_an_invalid_status_transition_is_rejected(self):
        session = workout_services.start_session(self.alice, workout=None)
        response = self.client.patch(
            reverse("api:session-detail", args=[session.pk]),
            {"status": "in_progress"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)


class OwnedResourceViewSetTests(APITestCase):
    """apps.api.viewsets.OwnedResourceViewSet, via the exercises endpoint
    — covers the shared ownership shape every OwnedResourceViewSet
    subclass gets for free."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)
        self.system_exercise = Exercise.objects.create(name="System Move", owner=None)
        self.bobs_exercise = Exercise.objects.create(name="Bob's Move", owner=self.bob)

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}

    def test_system_exercises_are_visible(self):
        response = self.client.get(
            reverse("api:exercise-detail", args=[self.system_exercise.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 200)

    def test_another_users_custom_exercise_is_not_visible(self):
        response = self.client.get(
            reverse("api:exercise-detail", args=[self.bobs_exercise.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_edit_a_system_exercise(self):
        response = self.client.patch(
            reverse("api:exercise-detail", args=[self.system_exercise.pk]),
            {"name": "Hijacked"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)

    def test_deleting_a_custom_exercise_soft_deletes_it(self):
        mine = Exercise.objects.create(name="My Move", owner=self.alice)
        response = self.client.delete(
            reverse("api:exercise-detail", args=[mine.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 204)
        mine.refresh_from_db()
        self.assertFalse(mine.active)
        self.assertTrue(Exercise.objects.filter(pk=mine.pk).exists())  # never hard-deleted


class ProgramHardDeleteTests(APITestCase):
    """Program is the one OwnedResourceViewSet with soft_delete=False —
    it has no `active` field and its web view really deletes."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)

    def test_deleting_a_program_actually_removes_it(self):
        from apps.programs.models import Program

        program = Program.objects.create(owner=self.alice, name="Delete Me")
        response = self.client.delete(
            reverse("api:program-detail", args=[program.pk]),
            HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}",
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Program.objects.filter(pk=program.pk).exists())


class ApiKeyManagementViewTests(TestCase):
    """The self-service, session-authenticated key management pages —
    apps.api.views_web."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("api_keys:key-list"))
        self.assertEqual(response.status_code, 302)

    def test_the_key_list_page_shows_api_documentation_with_the_real_host(self):
        """The "?" help button's modal (templates/api/key_list.html)
        — the base-URL/curl/Python examples show this deployment's
        actual host (request.scheme/get_host), not a placeholder, so
        they're copy-pasteable as-is."""
        response = self.client.get(reverse("api_keys:key-list"), SERVER_NAME="ironstack.example")
        self.assertContains(response, "http://ironstack.example/api/v1/")
        self.assertContains(response, "curl")
        self.assertContains(response, "import requests")

    def test_the_api_documentation_lists_every_context_and_its_endpoints(self):
        response = self.client.get(reverse("api_keys:key-list"))
        for endpoint in [
            "profile/", "exercises/", "programs/", "sessions/",
            "measurement-types/", "activity-types/", "foods/",
            "recipe-ingredients/", "diary-entries/", "nutrition-goals/",
            "records/", "analytics/summary/",
        ]:
            self.assertContains(response, f"<code>{endpoint}")

    def test_creating_a_key_shows_the_secret_exactly_once(self):
        import re

        data = {"name": "My new key", "exercises__can_read": "on"}
        create_response = self.client.post(reverse("api_keys:key-create"), data)
        self.assertEqual(create_response.status_code, 302)
        api_key = ApiKey.objects.get(user=self.alice)

        first_view = self.client.get(reverse("api_keys:key-created", args=[api_key.pk]))
        # The list page legitimately shows the short, non-secret prefix
        # ("isk_xxxxxxxx…") for identification — what must never appear
        # a second time is the *full* raw secret itself.
        match = re.search(r"isk_[A-Za-z0-9_-]{20,}", first_view.content.decode())
        self.assertIsNotNone(match, "the full raw secret should be shown on first view")
        raw_secret = match.group(0)

        second_view = self.client.get(reverse("api_keys:key-created", args=[api_key.pk]))
        self.assertRedirects(second_view, reverse("api_keys:key-list"))
        list_response = self.client.get(reverse("api_keys:key-list"))
        self.assertContains(list_response, api_key.prefix)  # the short prefix is fine to show
        self.assertNotContains(list_response, raw_secret)  # the full secret is never shown again

    def test_cannot_view_another_users_key_created_page(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        api_key, _secret = _create_key(bob)
        response = self.client.get(reverse("api_keys:key-created", args=[api_key.pk]))
        self.assertEqual(response.status_code, 404)

    def test_revoking_a_key_deletes_it(self):
        api_key, _secret = _create_key(self.alice)
        self.client.post(reverse("api_keys:key-revoke", args=[api_key.pk]))
        self.assertFalse(ApiKey.objects.filter(pk=api_key.pk).exists())

    def test_cannot_revoke_another_users_key(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        api_key, _secret = _create_key(bob)
        response = self.client.post(reverse("api_keys:key-revoke", args=[api_key.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ApiKey.objects.filter(pk=api_key.pk).exists())

    def test_creation_is_blocked_once_the_quota_is_reached(self):
        settings_obj = ApiSettings.load()
        settings_obj.max_api_keys_per_user = 1
        settings_obj.save()
        _create_key(self.alice)
        response = self.client.get(reverse("api_keys:key-create"))
        self.assertRedirects(response, reverse("api_keys:key-list"))
        self.assertEqual(ApiKey.objects.filter(user=self.alice).count(), 1)

    def test_permission_grid_creates_the_right_permission_rows(self):
        data = {"name": "Read-only exercises", "exercises__can_read": "on"}
        self.client.post(reverse("api_keys:key-create"), data)
        api_key = ApiKey.objects.get(user=self.alice)
        exercises_perm = api_key.permissions.get(context="exercises")
        self.assertTrue(exercises_perm.can_read)
        self.assertFalse(exercises_perm.can_create)
        programs_perm = api_key.permissions.get(context="programs")
        self.assertFalse(programs_perm.can_read)


class ApiKeyAdminTests(TestCase):
    def test_only_one_tier_can_be_the_default(self):
        from django.contrib.admin.sites import AdminSite

        from .admin import RateLimitTierAdmin

        first = RateLimitTier.objects.create(name="First", is_default=True)
        second = RateLimitTier.objects.create(name="Second", is_default=False)
        second.is_default = True

        admin_instance = RateLimitTierAdmin(RateLimitTier, AdminSite())
        admin_instance.save_model(request=None, obj=second, form=None, change=True)

        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(RateLimitTier.objects.get(pk=second.pk).is_default)


class ApiKeyPermissionModelTests(TestCase):
    def test_context_is_unique_per_key(self):
        from django.db import IntegrityError, transaction

        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        api_key, _secret = _create_key(alice)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ApiKeyPermission.objects.create(api_key=api_key, context="exercises")


class NutritionEndpointTests(APITestCase):
    """apps.api.views' Nutrition section — Food/MealSlot follow the
    same OwnedResourceViewSet shape OwnedResourceViewSetTests already
    covers generically (via exercises), so this focuses on what's
    specific to nutrition: the food/recipe XOR on a diary entry, FK
    ownership validation on recipe ingredients and diary entries, and
    goals/targets being read-only."""

    def setUp(self):
        from decimal import Decimal

        from apps.nutrition.models import Food, MealSlot, Recipe, ServingUnit

        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)
        self.food = Food.objects.create(
            owner=self.alice, name="Chicken", serving_size=Decimal("100"),
            serving_unit=ServingUnit.GRAM, calories=165, protein_grams=Decimal("31"),
            carbohydrate_grams=Decimal("0"), fat_grams=Decimal("3.6"),
        )
        self.bobs_food = Food.objects.create(
            owner=self.bob, name="Bob's food", serving_size=Decimal("100"),
            serving_unit=ServingUnit.GRAM, calories=100, protein_grams=Decimal("1"),
            carbohydrate_grams=Decimal("1"), fat_grams=Decimal("1"),
        )
        self.breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        self.recipe = Recipe.objects.create(owner=self.alice, name="Bowl", servings=2)
        self.bobs_recipe = Recipe.objects.create(owner=self.bob, name="Bob's Bowl", servings=1)

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}

    # -- Food --------------------------------------------------------

    def test_creating_a_food(self):
        response = self.client.post(
            reverse("api:food-list"),
            {
                "name": "Rice", "serving_size": "100", "serving_unit": "g",
                "calories": 130, "protein_grams": "2.7", "carbohydrate_grams": "28",
                "fat_grams": "0.3",
            },
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["owner"], self.alice.pk)

    def test_another_users_private_food_is_not_visible(self):
        response = self.client.get(
            reverse("api:food-detail", args=[self.bobs_food.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 404)

    def test_a_shared_food_is_visible_but_not_editable(self):
        from decimal import Decimal

        from apps.nutrition.models import Food, ServingUnit

        shared = Food.objects.create(
            owner=None, name="Shared food", serving_size=Decimal("100"),
            serving_unit=ServingUnit.GRAM, calories=50, protein_grams=Decimal("1"),
            carbohydrate_grams=Decimal("1"), fat_grams=Decimal("1"),
        )
        get_response = self.client.get(
            reverse("api:food-detail", args=[shared.pk]), **self._auth()
        )
        self.assertEqual(get_response.status_code, 200)
        patch_response = self.client.patch(
            reverse("api:food-detail", args=[shared.pk]), {"name": "Hijacked"},
            format="json", **self._auth(),
        )
        self.assertEqual(patch_response.status_code, 404)

    # -- Recipes / recipe ingredients ---------------------------------

    def test_creating_a_recipe(self):
        response = self.client.post(
            reverse("api:recipe-list"), {"name": "New Recipe", "servings": 4},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["owner"], self.alice.pk)

    def test_deleting_a_recipe_actually_removes_it(self):
        from apps.nutrition.models import Recipe

        response = self.client.delete(
            reverse("api:recipe-detail", args=[self.recipe.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Recipe.objects.filter(pk=self.recipe.pk).exists())

    def test_adding_an_ingredient_to_my_own_recipe(self):
        response = self.client.post(
            reverse("api:recipe-ingredient-list"),
            {"recipe": self.recipe.pk, "food": self.food.pk, "quantity": "150"},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201)

    def test_cannot_add_an_ingredient_to_another_users_recipe(self):
        response = self.client.post(
            reverse("api:recipe-ingredient-list"),
            {"recipe": self.bobs_recipe.pk, "food": self.food.pk, "quantity": "150"},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_add_another_users_private_food_as_an_ingredient(self):
        response = self.client.post(
            reverse("api:recipe-ingredient-list"),
            {"recipe": self.recipe.pk, "food": self.bobs_food.pk, "quantity": "150"},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    # -- Diary entries -------------------------------------------------

    def test_logging_a_food_to_the_diary(self):
        response = self.client.post(
            reverse("api:diary-entry-list"),
            {
                "date": "2026-01-01", "meal_slot": self.breakfast.pk,
                "food": self.food.pk, "quantity": "150",
            },
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["user"], self.alice.pk)

    def test_logging_a_recipe_to_the_diary(self):
        response = self.client.post(
            reverse("api:diary-entry-list"),
            {
                "date": "2026-01-01", "meal_slot": self.breakfast.pk,
                "recipe": self.recipe.pk, "quantity": "1",
            },
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201)

    def test_logging_both_food_and_recipe_is_rejected(self):
        response = self.client.post(
            reverse("api:diary-entry-list"),
            {
                "date": "2026-01-01", "meal_slot": self.breakfast.pk,
                "food": self.food.pk, "recipe": self.recipe.pk, "quantity": "1",
            },
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_logging_neither_food_nor_recipe_is_rejected(self):
        response = self.client.post(
            reverse("api:diary-entry-list"),
            {"date": "2026-01-01", "meal_slot": self.breakfast.pk, "quantity": "1"},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_log_another_users_recipe(self):
        response = self.client.post(
            reverse("api:diary-entry-list"),
            {
                "date": "2026-01-01", "meal_slot": self.breakfast.pk,
                "recipe": self.bobs_recipe.pk, "quantity": "1",
            },
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_another_users_diary_entry_is_not_visible(self):
        from apps.nutrition.models import DiaryEntry

        bobs_entry = DiaryEntry.objects.create(
            user=self.bob, date="2026-01-01", meal_slot=self.breakfast,
            food=self.bobs_food, quantity="100",
        )
        response = self.client.get(
            reverse("api:diary-entry-detail", args=[bobs_entry.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 404)

    # -- Goals/targets: read-only ---------------------------------------

    def test_nutrition_goals_are_read_only(self):
        from decimal import Decimal

        from apps.nutrition import services as nutrition_services

        goal = nutrition_services.set_goal(
            self.alice, goal_type="maintenance", target_rate_kg_per_week=Decimal("0")
        )
        list_response = self.client.get(reverse("api:nutrition-goal-list"), **self._auth())
        self.assertEqual(list_response.status_code, 200)
        post_response = self.client.post(
            reverse("api:nutrition-goal-list"),
            {"goal_type": "maintenance", "target_rate_kg_per_week": "0"},
            format="json", **self._auth(),
        )
        self.assertEqual(post_response.status_code, 405)
        patch_response = self.client.patch(
            reverse("api:nutrition-goal-detail", args=[goal.pk]),
            {"notes": "hijacked"}, format="json", **self._auth(),
        )
        self.assertEqual(patch_response.status_code, 405)

    def test_nutrition_targets_are_read_only(self):
        from decimal import Decimal

        from apps.nutrition import macros
        from apps.nutrition import services as nutrition_services

        goal = nutrition_services.set_goal(
            self.alice, goal_type="maintenance", target_rate_kg_per_week=Decimal("0")
        )
        macro_result = macros.calculate_macros(Decimal("80"), 2000, "maintenance")
        target = nutrition_services.set_target(
            self.alice, goal=goal, daily_calories=2000, macro_breakdown=macro_result,
            source="calculated", reason="test",
        )
        list_response = self.client.get(reverse("api:nutrition-target-list"), **self._auth())
        self.assertEqual(list_response.status_code, 200)
        delete_response = self.client.delete(
            reverse("api:nutrition-target-detail", args=[target.pk]), **self._auth()
        )
        self.assertEqual(delete_response.status_code, 405)

    def test_another_users_goals_are_not_visible(self):
        from decimal import Decimal

        from apps.nutrition import services as nutrition_services

        nutrition_services.set_goal(
            self.bob, goal_type="maintenance", target_rate_kg_per_week=Decimal("0")
        )
        response = self.client.get(reverse("api:nutrition-goal-list"), **self._auth())
        self.assertEqual(response.data["count"], 0)

    # -- Meal slots ------------------------------------------------------

    def test_system_meal_slots_are_visible_but_not_editable(self):
        get_response = self.client.get(
            reverse("api:meal-slot-detail", args=[self.breakfast.pk]), **self._auth()
        )
        self.assertEqual(get_response.status_code, 200)
        patch_response = self.client.patch(
            reverse("api:meal-slot-detail", args=[self.breakfast.pk]),
            {"name": "Hijacked"}, format="json", **self._auth(),
        )
        self.assertEqual(patch_response.status_code, 404)

    def test_creating_a_custom_meal_slot(self):
        response = self.client.post(
            reverse("api:meal-slot-list"), {"name": "Midnight snack"},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["owner"], self.alice.pk)


class NutritionProfileEndpointTests(APITestCase):
    """A singleton resource, same shape ProfileEndpointTests already
    covers for /profile/ — GET/PATCH always act on the authenticated
    key's own NutritionProfile."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}

    def test_no_profile_yet_is_a_404_not_a_crash(self):
        response = self.client.get(reverse("api:nutrition-profile"), **self._auth())
        self.assertEqual(response.status_code, 404)

    def test_reading_and_updating_my_own_profile(self):
        from apps.nutrition.models import NutritionProfile

        NutritionProfile.objects.create(
            user=self.alice, biological_sex="female", birth_date="1990-01-01",
            activity_job="sedentary", activity_level="light",
        )
        get_response = self.client.get(reverse("api:nutrition-profile"), **self._auth())
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.data["biological_sex"], "female")
        self.assertIn("age_years", get_response.data)

        patch_response = self.client.patch(
            reverse("api:nutrition-profile"), {"activity_level": "active"},
            format="json", **self._auth(),
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.data["activity_level"], "active")

    def test_another_users_profile_is_not_returned(self):
        from apps.nutrition.models import NutritionProfile

        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        NutritionProfile.objects.create(
            user=bob, biological_sex="male", birth_date="1990-01-01",
            activity_job="sedentary", activity_level="light",
        )
        response = self.client.get(reverse("api:nutrition-profile"), **self._auth())
        self.assertEqual(response.status_code, 404)


class DietPlanEndpointTests(APITestCase):
    """DietPlan's mutating actions all route through
    apps.nutrition.diet_builder/services rather than a raw
    ModelSerializer write — see DietPlanSerializer's own docstring.
    DietPlanMeal/DietPlanItem are read-only sub-resources."""

    def setUp(self):
        from decimal import Decimal

        from apps.nutrition.models import Food, MealSlot, ServingUnit

        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)
        Food.objects.create(
            owner=self.alice, name="Chicken", serving_size=Decimal("100"),
            serving_unit=ServingUnit.GRAM, calories=165, protein_grams=Decimal("31"),
            carbohydrate_grams=Decimal("0"), fat_grams=Decimal("3.6"),
        )
        self.breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        self.lunch = MealSlot.objects.get(name="Lunch", owner=None)

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}

    def _create_plan(self, **overrides):
        payload = {
            "name": "My plan", "target_calories": 2000,
            "target_protein_grams": "150", "target_carbohydrate_grams": "200",
            "target_fat_grams": "60", "meal_slots": [self.breakfast.pk, self.lunch.pk],
        }
        payload.update(overrides)
        return self.client.post(
            reverse("api:diet-plan-list"), payload, format="json", **self._auth()
        )

    def test_creating_a_diet_plan_builds_meals_and_items(self):
        response = self._create_plan()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data["meals"]), 2)
        self.assertTrue(any(meal["items"] for meal in response.data["meals"]))

    def test_creating_a_second_plan_deactivates_the_first(self):
        from apps.nutrition.models import DietPlan

        first = self._create_plan().data
        second = self._create_plan(name="Another plan").data
        self.assertFalse(DietPlan.objects.get(pk=first["id"]).is_active)
        self.assertTrue(DietPlan.objects.get(pk=second["id"]).is_active)

    def test_is_active_and_targets_are_read_only(self):
        plan_id = self._create_plan().data["id"]
        response = self.client.patch(
            reverse("api:diet-plan-detail", args=[plan_id]),
            {"is_active": False, "target_calories": 9999, "name": "Renamed"},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "Renamed")
        self.assertEqual(response.data["target_calories"], 2000)
        self.assertTrue(response.data["is_active"])

    def test_activate_and_deactivate_actions(self):
        from apps.nutrition.models import DietPlan

        first_id = self._create_plan().data["id"]
        second_id = self._create_plan(name="Another plan").data["id"]

        activate_response = self.client.post(
            reverse("api:diet-plan-activate", args=[first_id]), **self._auth()
        )
        self.assertEqual(activate_response.status_code, 200)
        self.assertTrue(DietPlan.objects.get(pk=first_id).is_active)
        self.assertFalse(DietPlan.objects.get(pk=second_id).is_active)

        deactivate_response = self.client.post(
            reverse("api:diet-plan-deactivate", args=[first_id]), **self._auth()
        )
        self.assertEqual(deactivate_response.status_code, 200)
        self.assertFalse(DietPlan.objects.get(pk=first_id).is_active)

    def test_apply_creates_diary_entries(self):
        from apps.nutrition.models import DiaryEntry

        plan_id = self._create_plan().data["id"]
        response = self.client.post(
            reverse("api:diet-plan-apply", args=[plan_id]), {"date": "2026-01-01"},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(DiaryEntry.objects.filter(user=self.alice, date="2026-01-01").exists())

    def test_apply_without_a_date_is_rejected(self):
        plan_id = self._create_plan().data["id"]
        response = self.client.post(
            reverse("api:diet-plan-apply", args=[plan_id]), {}, format="json", **self._auth()
        )
        self.assertEqual(response.status_code, 400)

    def test_apply_with_a_malformed_date_is_rejected(self):
        plan_id = self._create_plan().data["id"]
        response = self.client.post(
            reverse("api:diet-plan-apply", args=[plan_id]), {"date": "not-a-date"},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_deleting_a_diet_plan_cascades_to_meals_and_items(self):
        from apps.nutrition.models import DietPlanItem, DietPlanMeal

        plan_id = self._create_plan().data["id"]
        response = self.client.delete(
            reverse("api:diet-plan-detail", args=[plan_id]), **self._auth()
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(DietPlanMeal.objects.filter(diet_plan_id=plan_id).exists())
        self.assertFalse(DietPlanItem.objects.filter(diet_plan_meal__diet_plan_id=plan_id).exists())

    def test_another_users_plan_is_not_visible(self):
        from apps.nutrition.diet_builder import build_diet_plan

        bobs_plan = build_diet_plan(
            self.bob, name="Bob's plan", goal=None, target_calories=2000,
            target_protein_grams="150", target_carbohydrate_grams="200",
            target_fat_grams="60", meal_slots=[self.breakfast],
        )
        response = self.client.get(
            reverse("api:diet-plan-detail", args=[bobs_plan.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_use_another_users_goal(self):
        from decimal import Decimal

        from apps.nutrition import services as nutrition_services

        bobs_goal = nutrition_services.set_goal(
            self.bob, goal_type="maintenance", target_rate_kg_per_week=Decimal("0")
        )
        response = self._create_plan(goal=bobs_goal.pk)
        self.assertEqual(response.status_code, 400)

    def test_diet_plan_meals_and_items_are_read_only(self):
        plan_data = self._create_plan().data
        meal_id = plan_data["meals"][0]["id"]

        list_response = self.client.get(reverse("api:diet-plan-meal-list"), **self._auth())
        self.assertEqual(list_response.status_code, 200)
        post_response = self.client.post(
            reverse("api:diet-plan-meal-list"), {}, format="json", **self._auth()
        )
        self.assertEqual(post_response.status_code, 405)
        patch_response = self.client.patch(
            reverse("api:diet-plan-meal-detail", args=[meal_id]),
            {"target_calories": 1}, format="json", **self._auth(),
        )
        self.assertEqual(patch_response.status_code, 405)

        item_id = plan_data["meals"][0]["items"][0]["id"]
        item_post_response = self.client.post(
            reverse("api:diet-plan-item-list"), {}, format="json", **self._auth()
        )
        self.assertEqual(item_post_response.status_code, 405)
        item_patch_response = self.client.patch(
            reverse("api:diet-plan-item-detail", args=[item_id]),
            {"quantity": "1"}, format="json", **self._auth(),
        )
        self.assertEqual(item_patch_response.status_code, 405)

    def test_another_users_diet_plan_meals_are_not_visible(self):
        from apps.nutrition.diet_builder import build_diet_plan

        bobs_plan = build_diet_plan(
            self.bob, name="Bob's plan", goal=None, target_calories=2000,
            target_protein_grams="150", target_carbohydrate_grams="200",
            target_fat_grams="60", meal_slots=[self.breakfast],
        )
        bobs_meal = bobs_plan.meals.first()
        response = self.client.get(
            reverse("api:diet-plan-meal-detail", args=[bobs_meal.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 404)
