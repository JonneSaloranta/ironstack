import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.exercises.models import Exercise
from apps.programs.models import Program, Workout
from apps.records import services as records_services
from apps.records.models import PersonalRecord, PRType
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
        "stretching",
        "social",
        "coaching",
        "assistant",
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
        """apps.api migration 0002 - a fresh install has something to
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
    """apps.api.auth.ApiKeyAuthentication - exercised through a real view
    (the profile endpoint) rather than unit-testing the authenticator in
    isolation, since its whole job is only meaningful in that context."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.api_key, self.raw_secret = _create_key(self.alice)

    def test_a_deactivated_users_key_stops_working(self):
        self.alice.is_active = False
        self.alice.save(update_fields=["is_active"])
        response = self.client.get(
            reverse("api:profile"), HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        )
        self.assertEqual(response.status_code, 401)

    def test_last_used_is_written_at_most_once_a_minute(self):
        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}
        self.client.get(reverse("api:profile"), **auth)
        self.api_key.refresh_from_db()
        first = self.api_key.last_used_at
        self.client.get(reverse("api:profile"), **auth)
        self.api_key.refresh_from_db()
        self.assertEqual(self.api_key.last_used_at, first)

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
    """apps.api.permissions.HasContextPermission - exercised through the
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
        touch "exercises" - the whole point of context-scoped keys."""
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
        """The whole point of an admin-editable tier - bump the number
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

    def test_email_is_read_only(self):
        # An API key must not be enough to take over the account through a
        # password reset sent to a new address.
        self.alice.email = "alice@example.com"
        self.alice.save(update_fields=["email"])
        response = self.client.patch(
            reverse("api:profile"), {"email": "mallory@example.com"}, format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.email, "alice@example.com")
        self.assertEqual(response.data["email"], "alice@example.com")

    def test_patch_updates_social_privacy_settings(self):
        response = self.client.patch(
            reverse("api:profile"),
            {"allow_friend_requests": False, "allow_group_invites": False},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.allow_friend_requests)
        self.assertFalse(self.alice.allow_group_invites)


    def test_body_tracking_reminder_fields(self):
        # Issue #40: the web's setting, join date and reminder rule.
        response = self.client.get(reverse("api:profile"), **self._auth())
        self.assertIn("date_joined", response.data)
        self.assertTrue(response.data["body_tracking_reminders_enabled"])
        self.assertFalse(response.data["needs_body_tracking_reminder"])  # joined just now

        User.objects.filter(pk=self.alice.pk).update(
            date_joined=timezone.now() - timedelta(days=30)
        )
        response = self.client.get(reverse("api:profile"), **self._auth())
        self.assertTrue(response.data["needs_body_tracking_reminder"])

        response = self.client.patch(
            reverse("api:profile"),
            {"body_tracking_reminders_enabled": False, "date_joined": "2000-01-01T00:00:00Z"},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["needs_body_tracking_reminder"])
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.body_tracking_reminders_enabled)
        self.assertNotEqual(self.alice.date_joined.year, 2000)


class WorkoutLoggingEndpointTests(APITestCase):
    """The most important end-to-end path: logging a set via the API
    must trigger PR detection exactly like apps.workouts.views.set_log
    does for the web UI - see apps.api.views.ExerciseSetViewSet."""

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
        self.assertEqual(records_response.data["results"][0]["exercise_name"], "API Test Squat")

    def test_set_create_response_lists_the_new_records(self):
        # Issue #30: the web's PR toast, without diffing records/.
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)

        def post(weight):
            return self.client.post(
                reverse("api:set-list"),
                {"performed_exercise": performed.pk, "weight": weight, "reps": 5},
                format="json", **self._auth(),
            )

        first = post("100.00")
        self.assertEqual(first.status_code, 201)
        max_weight = [r for r in first.data["new_records"] if r["record_type"] == PRType.MAX_WEIGHT]
        self.assertEqual(len(max_weight), 1)
        self.assertEqual(Decimal(max_weight[0]["value"]), Decimal("100"))
        self.assertEqual(max_weight[0]["exercise_name"], "API Test Squat")

        lighter = post("50.00").data["new_records"]
        self.assertFalse([r for r in lighter if r["record_type"] == PRType.MAX_WEIGHT])

        detail = self.client.get(
            reverse("api:set-detail", args=[first.data["id"]]), **self._auth()
        )
        self.assertNotIn("new_records", detail.data)

    def _logged_set(self, weight="600"):
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        exercise_set = workout_services.log_set(performed, weight=Decimal(weight), reps=8)
        records_services.check_and_record_prs(exercise_set)
        return exercise_set

    def test_deleting_a_set_removes_the_records_it_set(self):
        # Issue #21: a mistyped set, deleted right away, used to stay a
        # personal record for good.
        exercise_set = self._logged_set()
        response = self.client.delete(
            reverse("api:set-detail", args=[exercise_set.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(PersonalRecord.objects.filter(user=self.alice).exists())

    def test_editing_a_set_rebuilds_its_records(self):
        exercise_set = self._logged_set()
        response = self.client.patch(
            reverse("api:set-detail", args=[exercise_set.pk]),
            {"weight": "60.00"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        record = PersonalRecord.objects.get(record_type=PRType.MAX_WEIGHT)
        self.assertEqual(record.value, Decimal("60"))

    def test_moving_a_set_to_another_exercise_rebuilds_both(self):
        exercise_set = self._logged_set()
        other_exercise = Exercise.objects.create(name="API Test Row", owner=None)
        other_performed = workout_services.add_performed_exercise(
            exercise_set.performed_exercise.session, other_exercise
        )
        self.client.patch(
            reverse("api:set-detail", args=[exercise_set.pk]),
            {"performed_exercise": other_performed.pk},
            format="json",
            **self._auth(),
        )
        self.assertFalse(PersonalRecord.objects.filter(exercise=self.exercise).exists())
        self.assertTrue(
            PersonalRecord.objects.filter(
                exercise=other_exercise, record_type=PRType.MAX_WEIGHT
            ).exists()
        )

    def test_deleting_a_session_removes_its_records(self):
        exercise_set = self._logged_set()
        response = self.client.delete(
            reverse("api:session-detail", args=[exercise_set.performed_exercise.session_id]),
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(PersonalRecord.objects.filter(user=self.alice).exists())

    def _session_ids(self, query):
        response = self.client.get(reverse("api:session-list") + query, **self._auth())
        self.assertEqual(response.status_code, 200)
        return {row["id"] for row in response.data["results"]}

    def test_sessions_can_be_filtered_by_status(self):
        # Issue #17: a client resuming after a restart finds its session
        # in progress with one request.
        done = workout_services.start_session(self.alice, workout=None)
        workout_services.complete_session(done)
        current = workout_services.start_session(self.alice, workout=None)

        self.assertEqual(self._session_ids("?status=in_progress"), {current.pk})
        self.assertEqual(self._session_ids("?status=completed"), {done.pk})
        self.assertEqual(self._session_ids(""), {done.pk, current.pk})

    def test_sessions_can_be_filtered_by_start_time(self):
        from datetime import datetime
        from datetime import timezone as dt_timezone

        september = workout_services.start_session(self.alice, workout=None)
        september.started_at = datetime(2026, 9, 15, 18, tzinfo=dt_timezone.utc)
        september.status = "completed"
        september.save()
        october = workout_services.start_session(self.alice, workout=None)
        october.started_at = datetime(2026, 10, 1, 12, tzinfo=dt_timezone.utc)
        october.save()

        self.assertEqual(self._session_ids("?started_after=2026-10-01"), {october.pk})
        self.assertEqual(self._session_ids("?started_before=2026-10-01"), {september.pk})
        self.assertEqual(
            self._session_ids("?started_after=2026-09-15T18:00:00Z&started_before=2026-09-16"),
            {september.pk},
        )

    def test_an_invalid_session_filter_is_a_400(self):
        for query in ("?status=paused", "?started_after=yesterday", "?started_before=2026-13-01"):
            response = self.client.get(reverse("api:session-list") + query, **self._auth())
            self.assertEqual(response.status_code, 400, query)

    def test_cannot_add_a_performed_exercise_referencing_another_users_private_exercise(self):
        # Regression, found live: PerformedExerciseSerializer's
        # `exercise` field had no ownership check at all - a bare
        # PrimaryKeyRelatedField accepts *any* row's id, private
        # custom exercises very much included, until validate_exercise
        # was added.
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        bobs_private_exercise = Exercise.objects.create(name="Bob's Secret Lift", owner=bob)
        session = workout_services.start_session(self.alice, workout=None)

        response = self.client.post(
            reverse("api:performed-exercise-list"),
            {"session": session.pk, "exercise": bobs_private_exercise.pk},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

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

    def test_an_rpe_outside_the_scale_is_a_400(self):
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        response = self.client.post(
            reverse("api:set-list"),
            {"performed_exercise": performed.pk, "weight": "100.00", "reps": 5, "rpe": "11"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("rpe", response.data)

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

    def test_performed_exercises_carry_the_name_they_were_logged_under(self):
        session = workout_services.start_session(self.alice, workout=None)
        workout_services.add_performed_exercise(session, self.exercise)
        logged_name = self.exercise.name
        self.exercise.name = "Renamed later"
        self.exercise.save()
        response = self.client.get(
            reverse("api:session-detail", args=[session.pk]), **self._auth()
        )
        self.assertEqual(response.data["performed_exercises"][0]["exercise_name"], logged_name)

    def test_a_completed_session_cannot_be_flipped_to_abandoned(self):
        session = workout_services.start_session(self.alice, workout=None)
        workout_services.complete_session(session)
        session.refresh_from_db()
        ended_at = session.ended_at

        response = self.client.patch(
            reverse("api:session-detail", args=[session.pk]),
            {"status": "abandoned"},
            format="json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 400)
        session.refresh_from_db()
        self.assertEqual(session.status, "completed")
        self.assertEqual(session.ended_at, ended_at)

    def test_resending_a_finished_sessions_own_status_changes_nothing(self):
        session = workout_services.start_session(self.alice, workout=None)
        workout_services.complete_session(session)
        session.refresh_from_db()
        ended_at = session.ended_at

        response = self.client.patch(
            reverse("api:session-detail", args=[session.pk]),
            {"status": "completed"},
            format="json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.ended_at, ended_at)

    def test_an_invalid_status_transition_is_rejected(self):
        session = workout_services.start_session(self.alice, workout=None)
        response = self.client.patch(
            reverse("api:session-detail", args=[session.pk]),
            {"status": "in_progress"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)


class ExercisePrescriptionOwnershipTests(APITestCase):
    """Regression, found live going through every endpoint by hand:
    ExercisePrescriptionSerializer's `exercise` field had no ownership
    check at all - see its own validate_exercise for the fix and why
    it matters even though the raw API response never nests the
    exercise's own details (the web UI trusts this check already
    happened by the time it renders a prescription it owns)."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.bobs_private_exercise = Exercise.objects.create(
            name="Bob's Secret Lift", owner=self.bob
        )
        self.program = Program.objects.create(owner=self.alice, name="Alice Program")
        self.workout = Workout.objects.create(program=self.program, name="Day 1")
        _api_key, self.raw_secret = _create_key(self.alice)

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}

    def test_cannot_add_a_prescription_referencing_another_users_private_exercise(self):
        response = self.client.post(
            reverse("api:prescription-list"),
            {
                "workout": self.workout.pk,
                "exercise": self.bobs_private_exercise.pk,
                "order": 0,
                "set_count": 3,
                "min_reps": 5,
                "max_reps": 5,
            },
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_a_system_exercise_is_still_usable(self):
        # The fix must not overcorrect into blocking every exercise -
        # shared, system-seeded ones (owner=None) stay usable by anyone.
        system_exercise = Exercise.objects.create(name="System Squat", owner=None)
        response = self.client.post(
            reverse("api:prescription-list"),
            {
                "workout": self.workout.pk,
                "exercise": system_exercise.pk,
                "order": 0,
                "set_count": 3,
                "min_reps": 5,
                "max_reps": 5,
            },
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["exercise_name"], "System Squat")

    def test_prescriptions_carry_the_exercise_name_wherever_they_appear(self):
        # Issue #19: a client shouldn't need one request per exercise to
        # render a workout.
        from apps.programs.models import ExercisePrescription

        exercise = Exercise.objects.create(name="System Bench", owner=None)
        prescription = ExercisePrescription.objects.create(
            workout=self.workout, exercise=exercise
        )

        program = self.client.get(
            reverse("api:program-detail", args=[self.program.pk]), **self._auth()
        )
        workout = self.client.get(
            reverse("api:workout-detail", args=[self.workout.pk]), **self._auth()
        )
        detail = self.client.get(
            reverse("api:prescription-detail", args=[prescription.pk]), **self._auth()
        )

        self.assertEqual(
            program.data["workouts"][0]["prescriptions"][0]["exercise_name"], "System Bench"
        )
        self.assertEqual(workout.data["prescriptions"][0]["exercise_name"], "System Bench")
        self.assertEqual(detail.data["exercise_name"], "System Bench")


class UpNextEndpointTests(APITestCase):
    """Issue #16 - the dashboard's "Up next" workout, for a client."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.program = Program.objects.create(
            owner=self.alice, name="Rotation", is_continuous=True
        )
        self.day_a = Workout.objects.create(program=self.program, name="A", order=0)
        self.day_b = Workout.objects.create(program=self.program, name="B", order=1)

    def _get(self, **permission_overrides):
        _api_key, raw_secret = _create_key(self.alice, **permission_overrides)
        return self.client.get(reverse("api:up-next"), HTTP_AUTHORIZATION=f"Bearer {raw_secret}")

    def test_nothing_next_before_training_from_a_program(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"program": None, "workout": None, "day": None})

    def test_the_workout_after_the_last_completed_one(self):
        session = workout_services.start_session(self.alice, workout=self.day_a)
        workout_services.complete_session(session)

        response = self._get()

        self.assertEqual(response.data["program"]["id"], self.program.pk)
        self.assertEqual(response.data["program"]["name"], "Rotation")
        self.assertEqual(response.data["workout"]["id"], self.day_b.pk)
        self.assertEqual(response.data["workout"]["name"], "B")
        self.assertIsNone(response.data["day"])

    def test_needs_programs_read_permission(self):
        response = self._get(
            programs={
                "can_create": False,
                "can_read": False,
                "can_update": False,
                "can_delete": False,
            }
        )
        self.assertEqual(response.status_code, 403)


class IdempotencyKeyTests(APITestCase):
    """Issue #22 - a retried write must not log the same set twice."""

    def setUp(self):
        from apps.workouts.models import ExerciseSet

        self.ExerciseSet = ExerciseSet
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="API Test Squat", owner=None)
        session = workout_services.start_session(self.alice, workout=None)
        self.performed = workout_services.add_performed_exercise(session, self.exercise)
        _api_key, self.raw_secret = _create_key(self.alice)

    def _post_set(self, key=None, weight="100.00", secret=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {secret or self.raw_secret}"}
        if key is not None:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        return self.client.post(
            reverse("api:set-list"),
            {"performed_exercise": self.performed.pk, "weight": weight, "reps": 5},
            format="json",
            **headers,
        )

    def test_a_retry_with_the_same_key_gets_the_first_response(self):
        first = self._post_set("3f0c6a52")
        retry = self._post_set("3f0c6a52")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(retry.data, first.data)
        self.assertEqual(retry["Idempotent-Replayed"], "true")
        self.assertEqual(self.ExerciseSet.objects.count(), 1)

    def test_without_a_key_every_request_creates(self):
        self._post_set()
        self._post_set()
        self.assertEqual(self.ExerciseSet.objects.count(), 2)

    def test_different_keys_create_separately(self):
        self._post_set("a")
        self._post_set("b")
        self.assertEqual(self.ExerciseSet.objects.count(), 2)

    def test_the_same_key_for_a_different_request_is_refused(self):
        self._post_set("3f0c6a52")
        response = self._post_set("3f0c6a52", weight="120.00")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.ExerciseSet.objects.count(), 1)

    def test_keys_are_per_user(self):
        from apps.api.models import IdempotencyKey

        self._post_set("shared")
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _bob_key, bob_secret = _create_key(bob)
        response = self._post_set("shared", secret=bob_secret)
        # Bob can't log on Alice's exercise, but his key is his own: his
        # request ran (and failed) instead of replaying Alice's response.
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IdempotencyKey.objects.filter(user=bob).exists())

    def test_a_failed_request_is_not_remembered(self):
        failed = self._post_set("3f0c6a52", weight="not a number")
        retried = self._post_set("3f0c6a52")
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(retried.status_code, 201)
        self.assertEqual(self.ExerciseSet.objects.count(), 1)

    def test_an_expired_key_runs_the_request_again(self):
        from apps.api.idempotency import KEY_LIFETIME
        from apps.api.models import IdempotencyKey

        self._post_set("3f0c6a52")
        IdempotencyKey.objects.update(created_at=timezone.now() - KEY_LIFETIME - timedelta(1))
        response = self._post_set("3f0c6a52")

        self.assertEqual(response.status_code, 201)
        self.assertNotIn("Idempotent-Replayed", response)
        self.assertEqual(self.ExerciseSet.objects.count(), 2)
        self.assertEqual(IdempotencyKey.objects.count(), 1)

    def test_the_same_key_arriving_twice_at_once_still_creates_once(self):
        # The second request misses the stored key (both looked before
        # either had stored it); the unique constraint then rolls its
        # set back and it answers with the first request's response.
        from unittest import mock

        from django.db.models.query import QuerySet

        from apps.api.models import IdempotencyKey

        first = self._post_set("3f0c6a52")
        original_first = QuerySet.first
        missed = []

        def first_missing_the_key_once(queryset):
            if queryset.model is IdempotencyKey and not missed:
                missed.append(True)
                return None
            return original_first(queryset)

        with mock.patch.object(QuerySet, "first", first_missing_the_key_once):
            retry = self._post_set("3f0c6a52")

        self.assertTrue(missed)
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(retry.data, first.data)
        self.assertEqual(self.ExerciseSet.objects.count(), 1)

    def test_an_overlong_key_is_a_400(self):
        self.assertEqual(self._post_set("x" * 256).status_code, 400)

    def test_sessions_and_performed_exercises_honour_it_too(self):
        auth = {
            "HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}",
            "HTTP_IDEMPOTENCY_KEY": "start-1",
        }
        first = self.client.post(reverse("api:session-list"), {}, format="json", **auth)
        retry = self.client.post(reverse("api:session-list"), {}, format="json", **auth)
        self.assertEqual(retry.data["id"], first.data["id"])

        auth["HTTP_IDEMPOTENCY_KEY"] = "add-1"
        body = {"session": first.data["id"], "exercise": self.exercise.pk}
        url = reverse("api:performed-exercise-list")
        first = self.client.post(url, body, format="json", **auth)
        retry = self.client.post(url, body, format="json", **auth)
        self.assertEqual(retry.data["id"], first.data["id"])


class IdempotentQuickLogTests(APITestCase):
    """Issue #31 - the other quick logs a mobile client queues offline
    honour Idempotency-Key the same way sets/ does."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)

    def _post_twice(self, url_name, payload):
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}",
            "HTTP_IDEMPOTENCY_KEY": f"key-{url_name}",
        }
        first = self.client.post(reverse(url_name), payload, format="json", **headers)
        retry = self.client.post(reverse(url_name), payload, format="json", **headers)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(retry["Idempotent-Replayed"], "true")
        self.assertEqual(retry.data, first.data)

    def test_quick_log_creates_are_idempotent(self):
        from apps.activities.models import Activity, ActivityType
        from apps.measurements.models import BodyMeasurement, MeasurementType
        from apps.nutrition.models import DiaryEntry, MealSlot
        from apps.stretching.models import StretchSession

        breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        self._post_twice(
            "api:diary-entry-list",
            {"date": "2026-10-01", "meal_slot": breakfast.pk, "quantity": "1",
             "quick_calories": 300},
        )
        weight = MeasurementType.objects.get(name="Body weight", owner=None)
        self._post_twice("api:measurement-list", {"measurement_type": weight.pk, "value": "80"})
        cycling = ActivityType.objects.get(name="Cycling", owner=None)
        self._post_twice(
            "api:activity-list",
            {"activity_type": cycling.pk, "date": "2026-10-01", "duration": "00:30:00"},
        )
        self._post_twice("api:stretch-session-list", {"duration": "00:10:00"})

        self.assertEqual(DiaryEntry.objects.filter(user=self.alice).count(), 1)
        self.assertEqual(BodyMeasurement.objects.filter(user=self.alice).count(), 1)
        self.assertEqual(Activity.objects.filter(user=self.alice).count(), 1)
        self.assertEqual(StretchSession.objects.filter(user=self.alice).count(), 1)


class SuggestionEndpointTests(APITestCase):
    """Issue #15 - the web's smart weight suggestion, for a client."""

    def setUp(self):
        from apps.programs.models import ExercisePrescription

        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="API Test Bench", owner=None)
        program = Program.objects.create(owner=self.alice, name="Program")
        self.workout = Workout.objects.create(program=program, name="Day 1")
        ExercisePrescription.objects.create(
            workout=self.workout,
            exercise=self.exercise,
            set_count=3,
            min_reps=5,
            max_reps=5,
            target_weight=Decimal("60"),
            progression_method="linear",
            weight_increment=Decimal("2.5"),
        )
        _api_key, self.raw_secret = _create_key(self.alice)

    def _suggestion(self):
        session = workout_services.start_session(self.alice, workout=self.workout)
        performed = session.performed_exercises.get()
        return self.client.get(
            reverse("api:performed-exercise-suggestion", args=[performed.pk]),
            HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}",
        )

    def test_without_history_it_suggests_the_prescribed_target(self):
        response = self._suggestion()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["weight"], "60.00")
        self.assertEqual(response.data["min_reps"], 5)
        self.assertEqual(response.data["method"], "linear")
        self.assertEqual(response.data["action"], "insufficient_data")
        self.assertEqual(response.data["confidence"], "low")
        self.assertTrue(response.data["reason"])

    def test_after_a_successful_session_it_adds_the_increment(self):
        session = workout_services.start_session(self.alice, workout=self.workout)
        performed = session.performed_exercises.get()
        for _ in range(3):
            workout_services.log_set(performed, weight=Decimal("60"), reps=5)
        workout_services.complete_session(session)

        response = self._suggestion()

        self.assertEqual(response.data["weight"], "62.50")
        self.assertEqual(response.data["action"], "increase")
        self.assertEqual(response.data["confidence"], "medium")

    def test_the_reason_is_in_the_users_language(self):
        english = self._suggestion().data["reason"]
        self.alice.language = "fi"
        self.alice.save()
        self.assertNotEqual(self._suggestion().data["reason"], english)

    def test_no_prescription_means_no_suggestion(self):
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        response = self.client.get(
            reverse("api:performed-exercise-suggestion", args=[performed.pk]),
            HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data.values()), {None})


class LastPerformanceEndpointTests(APITestCase):
    """Issue #20 - "last time" for an exercise, across sessions."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="API Test Press", owner=None)
        _api_key, self.raw_secret = _create_key(self.alice)

    def _get(self, exercise=None):
        return self.client.get(
            reverse("api:exercise-last-performance", args=[(exercise or self.exercise).pk]),
            HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}",
        )

    def _session(self, sets, complete=True):
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        for weight, reps, is_warmup in sets:
            workout_services.log_set(
                performed, weight=Decimal(weight), reps=reps, is_warmup=is_warmup
            )
        if complete:
            workout_services.complete_session(session)
        return session

    def test_never_performed(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"session": None, "started_at": None, "sets": []})

    def test_the_most_recent_completed_session_with_sets(self):
        self._session([("70", 5, False)])
        latest = self._session([("40", 8, True), ("80", 5, False), ("80", 5, False)])
        self._session([])  # completed, but nothing logged for it
        self._session([("200", 1, False)], complete=False)  # still in progress

        response = self._get()

        self.assertEqual(response.data["session"], latest.pk)
        self.assertEqual(
            [(row["weight"], row["reps"], row["is_warmup"]) for row in response.data["sets"]],
            [("40.00", 8, True), ("80.00", 5, False), ("80.00", 5, False)],
        )

    def test_another_users_private_exercise_is_a_404(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        bobs = Exercise.objects.create(name="Bob's Lift", owner=bob)
        self.assertEqual(self._get(bobs).status_code, 404)


class ExerciseListQueryTests(APITestCase):
    """Issue #18 - an exercise picker shouldn't need one request per
    page of the whole library."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)

    def _get(self, query):
        response = self.client.get(
            reverse("api:exercise-list") + query, HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        )
        return response

    def test_search_matches_the_name(self):
        Exercise.objects.create(name="Zzq Test Curl", owner=self.alice)
        response = self._get("?search=zzq test")
        self.assertEqual([row["name"] for row in response.data["results"]], ["Zzq Test Curl"])

    def test_search_matches_the_name_translated_to_the_users_language(self):
        # The seeded "Barbell Row" is "Kulmasoutu tangolla" in Finnish.
        self.alice.language = "en"
        self.alice.save()
        self.assertEqual(self._get("?search=kulmasoutu").data["count"], 0)

        self.alice.language = "fi"
        self.alice.save()
        response = self._get("?search=kulmasoutu")
        self.assertIn("Barbell Row", [row["name"] for row in response.data["results"]])

    def test_ids_fetches_a_known_set(self):
        first = Exercise.objects.create(name="Test A", owner=self.alice)
        second = Exercise.objects.create(name="Test B", owner=self.alice)
        Exercise.objects.create(name="Test C", owner=self.alice)
        response = self._get(f"?ids={first.pk},{second.pk}")
        self.assertEqual({row["id"] for row in response.data["results"]}, {first.pk, second.pk})

    def test_ids_never_reach_another_users_exercise(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        bobs = Exercise.objects.create(name="Bob's Lift", owner=bob)
        response = self._get(f"?ids={bobs.pk}")
        self.assertEqual(response.data["count"], 0)

    def test_malformed_ids_are_a_400(self):
        self.assertEqual(self._get("?ids=1,two").status_code, 400)

    def test_page_size_is_honoured_up_to_the_cap(self):
        for index in range(110):
            Exercise.objects.create(name=f"Test {index}", owner=self.alice)
        self.assertEqual(len(self._get("").data["results"]), 25)
        self.assertEqual(len(self._get("?page_size=5").data["results"]), 5)
        self.assertEqual(len(self._get("?page_size=1000").data["results"]), 100)


class OwnedResourceViewSetTests(APITestCase):
    """apps.api.viewsets.OwnedResourceViewSet, via the exercises endpoint
    - covers the shared ownership shape every OwnedResourceViewSet
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


class ExerciseInstructionsAndImagesFieldsTests(APITestCase):
    """apps.api.serializers.ExerciseSerializer's own `instructions`/
    `instructions_attribution`/`images` fields - added alongside the
    web app's own exercise-images feature (apps.exercises.models.
    ExerciseImage), so a machine client can read the same instructional
    content the server-rendered detail page shows."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}

    def test_instructions_is_writable(self):
        mine = Exercise.objects.create(name="My Move", owner=self.alice)
        response = self.client.patch(
            reverse("api:exercise-detail", args=[mine.pk]),
            {"instructions": "Step one.\nStep two."},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        mine.refresh_from_db()
        self.assertEqual(mine.instructions, "Step one.\nStep two.")

    def test_instructions_attribution_is_read_only(self):
        mine = Exercise.objects.create(
            name="My Move", owner=self.alice, instructions_attribution="Someone - CC-BY-SA"
        )
        response = self.client.patch(
            reverse("api:exercise-detail", args=[mine.pk]),
            {"instructions_attribution": "Hijacked"},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        mine.refresh_from_db()
        self.assertEqual(mine.instructions_attribution, "Someone - CC-BY-SA")

    def test_images_are_included_read_only(self):
        from apps.exercises.models import ExerciseImage

        mine = Exercise.objects.create(name="My Move", owner=self.alice)
        ExerciseImage.objects.create(exercise=mine, caption="Step 1", attribution="Someone")
        response = self.client.get(
            reverse("api:exercise-detail", args=[mine.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["images"]), 1)
        self.assertEqual(response.data["images"][0]["caption"], "Step 1")


class ProgramHardDeleteTests(APITestCase):
    """Program is the one OwnedResourceViewSet with soft_delete=False -
    it has no `active` field and its web view really deletes."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)

    def test_copying_a_program_copies_workouts_and_prescriptions(self):
        # Issue #32.
        from apps.programs.models import ExercisePrescription, Program, Workout

        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        template = Program.objects.create(owner=None, name="Template", is_template=True)
        workout = Workout.objects.create(program=template, name="Day A")
        squat = Exercise.objects.create(name="API Copy Squat", owner=None)
        ExercisePrescription.objects.create(workout=workout, exercise=squat)
        response = self.client.post(
            reverse("api:program-copy", args=[template.pk]),
            HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}",
        )
        self.assertEqual(response.status_code, 201, response.data)
        copy = Program.objects.get(pk=response.data["id"])
        self.assertEqual(copy.owner, self.alice)
        self.assertEqual(copy.workouts.get().prescriptions.get().exercise, squat)
        bobs = Program.objects.create(owner=bob, name="Bob's")
        hidden = self.client.post(
            reverse("api:program-copy", args=[bobs.pk]),
            HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}",
        )
        self.assertEqual(hidden.status_code, 404)

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
    """The self-service, session-authenticated key management pages -
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
        - the base-URL/curl/Python examples show this deployment's
        actual host (request.scheme/get_host), not a placeholder, so
        they're copy-pasteable as-is."""
        response = self.client.get(reverse("api_keys:key-list"), SERVER_NAME="ironstack.example")
        self.assertContains(response, "http://ironstack.example/api/v1/")
        self.assertContains(response, "curl")
        self.assertContains(response, "import requests")

    def test_the_api_documentation_lists_every_context_and_its_endpoints(self):
        """Regression: nutrition/profile/ and the diet-plans/diet-plan-
        meals/diet-plan-items/ endpoints (apps/api/urls.py) went live
        without this in-app help table ever being updated to mention
        them - real endpoints an API key could already call, with no
        documentation of them anywhere a user would actually look."""
        response = self.client.get(reverse("api_keys:key-list"))
        for endpoint in [
            "profile/", "exercises/", "programs/", "sessions/",
            "measurement-types/", "activity-types/", "foods/",
            "recipe-ingredients/", "diary-entries/", "nutrition-goals/",
            "records/", "analytics/summary/", "nutrition/profile/",
            "diet-plans/", "diet-plan-meals/", "diet-plan-items/",
            "stretches/", "stretch-routines/", "stretch-routine-items/",
            "stretch-sessions/",
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
        # ("isk_xxxxxxxx…") for identification - what must never appear
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

    def test_every_permission_checkbox_is_named_for_screen_readers(self):
        # The grid has no <label> per checkbox (verb = column header,
        # context = row header), so each carries its own aria-label.
        response = self.client.get(reverse("api_keys:key-create"))
        self.assertContains(response, 'aria-label="Read - Exercises"')
        self.assertContains(response, 'aria-label="Delete - Profile"')

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
    """apps.api.views' Nutrition section - Food/MealSlot follow the
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

    def test_deleting_a_recipe_used_in_the_diary_only_hides_it(self):
        from datetime import date
        from decimal import Decimal

        from apps.nutrition.models import DiaryEntry

        entry = DiaryEntry.objects.create(
            user=self.alice, date=date(2026, 1, 1), meal_slot=self.breakfast,
            recipe=self.recipe, quantity=Decimal("1"),
        )
        response = self.client.delete(
            reverse("api:recipe-detail", args=[self.recipe.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 204)
        self.assertTrue(DiaryEntry.objects.filter(pk=entry.pk).exists())
        self.assertEqual(
            self.client.get(
                reverse("api:recipe-detail", args=[self.recipe.pk]), **self._auth()
            ).status_code,
            404,
        )

    def _builtin_recipe(self):
        from decimal import Decimal

        from apps.nutrition.models import Recipe, RecipeIngredient

        recipe = Recipe.objects.create(owner=None, name="Template bowl", servings=1)
        RecipeIngredient.objects.create(recipe=recipe, food=self.food, quantity=Decimal("100"))
        return recipe

    def test_builtin_recipes_are_listed_and_readable(self):
        # Issue #38: diet plans are built from template recipes.
        builtin = self._builtin_recipe()
        list_response = self.client.get(reverse("api:recipe-list"), **self._auth())
        ids = {row["id"] for row in list_response.data["results"]}
        self.assertIn(builtin.pk, ids)
        self.assertIn(self.recipe.pk, ids)
        self.assertNotIn(self.bobs_recipe.pk, ids)
        detail = self.client.get(reverse("api:recipe-detail", args=[builtin.pk]), **self._auth())
        self.assertEqual(detail.status_code, 200)
        self.assertIsNone(detail.data["owner"])
        self.assertEqual(len(detail.data["ingredients"]), 1)
        ingredient_id = detail.data["ingredients"][0]["id"]
        self.assertEqual(
            self.client.get(
                reverse("api:recipe-ingredient-detail", args=[ingredient_id]), **self._auth()
            ).status_code,
            200,
        )

    def test_builtin_recipes_are_not_writable(self):
        builtin = self._builtin_recipe()
        url = reverse("api:recipe-detail", args=[builtin.pk])
        self.assertEqual(
            self.client.patch(url, {"name": "Hijacked"}, format="json", **self._auth()).status_code,
            404,
        )
        self.assertEqual(self.client.delete(url, **self._auth()).status_code, 404)
        ingredient = builtin.ingredients.get()
        self.assertEqual(
            self.client.patch(
                reverse("api:recipe-ingredient-detail", args=[ingredient.pk]),
                {"quantity": "1"}, format="json", **self._auth(),
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("api:recipe-ingredient-list"),
                {"recipe": builtin.pk, "food": self.food.pk, "quantity": "10"},
                format="json", **self._auth(),
            ).status_code,
            400,
        )

    def test_logging_a_builtin_recipe_to_the_diary(self):
        builtin = self._builtin_recipe()
        response = self.client.post(
            reverse("api:diary-entry-list"),
            {
                "date": "2026-01-01", "meal_slot": self.breakfast.pk,
                "recipe": builtin.pk, "quantity": "1",
            },
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201)

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

    def test_explicit_null_recipe_or_food_is_accepted(self):
        # Issue #39: an explicit null used to crash validate_<fk> with a 500.
        food_response = self.client.post(
            reverse("api:diary-entry-list"),
            {
                "date": "2026-01-01", "meal_slot": self.breakfast.pk,
                "food": self.food.pk, "recipe": None, "quantity": "100",
            },
            format="json", **self._auth(),
        )
        self.assertEqual(food_response.status_code, 201)
        recipe_response = self.client.post(
            reverse("api:diary-entry-list"),
            {
                "date": "2026-01-01", "meal_slot": self.breakfast.pk,
                "food": None, "recipe": self.recipe.pk, "quantity": "1",
            },
            format="json", **self._auth(),
        )
        self.assertEqual(recipe_response.status_code, 201)
        quick_response = self.client.post(
            reverse("api:diary-entry-list"),
            {
                "date": "2026-01-01", "meal_slot": self.breakfast.pk, "food": None,
                "recipe": None, "quantity": "1", "quick_calories": 300,
            },
            format="json", **self._auth(),
        )
        self.assertEqual(quick_response.status_code, 201)
        patch_response = self.client.patch(
            reverse("api:diary-entry-detail", args=[food_response.data["id"]]),
            {"recipe": None, "quantity": "120"}, format="json", **self._auth(),
        )
        self.assertEqual(patch_response.status_code, 200)

    def test_entry_nutrition_comes_from_the_logged_snapshot(self):
        # Issue #27: editing the food later must not change a past day.
        response = self.client.post(
            reverse("api:diary-entry-list"),
            {
                "date": "2026-01-01", "meal_slot": self.breakfast.pk,
                "food": self.food.pk, "quantity": "200",
            },
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["food_name"], "Chicken")
        self.assertIsNone(response.data["recipe_name"])
        self.assertEqual(Decimal(response.data["nutrition"]["calories"]), Decimal("330"))
        self.food.calories = 1000
        self.food.save()
        entry = self.client.get(
            reverse("api:diary-entry-detail", args=[response.data["id"]]), **self._auth()
        )
        self.assertEqual(Decimal(entry.data["nutrition"]["calories"]), Decimal("330"))
        totals = self.client.get(
            reverse("api:nutrition-daily-totals"), {"date": "2026-01-01"}, **self._auth()
        )
        self.assertEqual(totals.status_code, 200)
        self.assertEqual(totals.data["date"], "2026-01-01")
        self.assertEqual(Decimal(totals.data["totals"]["calories"]), Decimal("330"))
        self.assertEqual(Decimal(totals.data["totals"]["protein_grams"]), Decimal("62"))

    def test_entry_of_a_deleted_recipe_keeps_name_and_nutrition_and_is_copied(self):
        from apps.nutrition import services as nutrition_services
        from apps.nutrition.models import DiaryEntry, RecipeIngredient

        RecipeIngredient.objects.create(recipe=self.recipe, food=self.food, quantity=Decimal("200"))
        entry = DiaryEntry.objects.create(
            user=self.alice, date="2026-01-01", meal_slot=self.breakfast,
            recipe=self.recipe, quantity=Decimal("1"),
        )
        nutrition_services.delete_recipe(self.recipe)  # used, so only hidden
        detail = self.client.get(
            reverse("api:diary-entry-detail", args=[entry.pk]), **self._auth()
        )
        self.assertEqual(detail.data["recipe_name"], "Bowl")
        self.assertEqual(Decimal(detail.data["nutrition"]["calories"]), Decimal("165"))
        # Resending the unchanged recipe while editing the quantity works.
        patch = self.client.patch(
            reverse("api:diary-entry-detail", args=[entry.pk]),
            {"recipe": self.recipe.pk, "quantity": "2"}, format="json", **self._auth(),
        )
        self.assertEqual(patch.status_code, 200, patch.data)
        copy = self.client.post(
            reverse("api:diary-entry-copy-day"),
            {"source_date": "2026-01-01", "target_date": "2026-01-02"},
            format="json", **self._auth(),
        )
        self.assertEqual(copy.status_code, 201)
        self.assertEqual(copy.data["copied"], 1)
        copied = DiaryEntry.objects.get(user=self.alice, date="2026-01-02")
        self.assertEqual(copied.recipe_id, self.recipe.pk)

    def test_null_food_and_recipe_without_quick_macros_is_rejected(self):
        response = self.client.post(
            reverse("api:diary-entry-list"),
            {
                "date": "2026-01-01", "meal_slot": self.breakfast.pk,
                "food": None, "recipe": None, "quantity": "1",
            },
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

    def test_nutrition_goals_are_never_edited(self):
        from decimal import Decimal

        from apps.nutrition import services as nutrition_services

        goal = nutrition_services.set_goal(
            self.alice, goal_type="maintenance", target_rate_kg_per_week=Decimal("0")
        )
        list_response = self.client.get(reverse("api:nutrition-goal-list"), **self._auth())
        self.assertEqual(list_response.status_code, 200)
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
    covers for /profile/ - GET/PATCH always act on the authenticated
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
    ModelSerializer write - see DietPlanSerializer's own docstring.
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


class InteractiveDocsTests(TestCase):
    """apps.api.urls_docs / apps.api.views_docs - docs/API.md
    "Interactive docs". Session-authenticated (login_required), a
    deliberately separate credential from the Bearer API keys the
    documented endpoints themselves need - see urls_docs' own
    docstring for why."""

    def setUp(self):
        self.alice = get_user_model().objects.create_user(
            username="alice", password="s3cret-pass"
        )

    def test_swagger_ui_requires_login(self):
        response = self.client.get(reverse("api_docs:swagger-ui"))
        self.assertEqual(response.status_code, 302)

    def test_schema_requires_login(self):
        response = self.client.get(reverse("api_docs:schema"))
        self.assertEqual(response.status_code, 302)

    def test_bootstrap_script_requires_login(self):
        response = self.client.get(reverse("api_docs:swagger-ui-bootstrap"))
        self.assertEqual(response.status_code, 302)

    def test_swagger_ui_loads_once_logged_in(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("api_docs:swagger-ui"))
        self.assertEqual(response.status_code, 200)

    def test_swagger_ui_has_no_inline_script(self):
        # Regression: drf-spectacular's own default template bootstraps
        # the UI from an inline <script>...</script> block, silently
        # blocked by this app's CSP (script-src has no 'unsafe-inline'
        # - apps.core.middleware.ContentSecurityPolicyMiddleware). See
        # apps.api.views_docs' own docstring for the same-origin
        # <script src> it uses instead.
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("api_docs:swagger-ui"))
        body = response.content.decode()
        self.assertNotRegex(body, r"<script>\s*\S")
        self.assertIn(reverse("api_docs:swagger-ui-bootstrap"), body)

    def test_bootstrap_script_is_valid_javascript_response(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("api_docs:swagger-ui-bootstrap"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertIn("SwaggerUIBundle", response.content.decode())

    def test_schema_loads_once_logged_in_and_documents_api_key_auth(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("api_docs:schema"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # ApiKeyAuthenticationScheme (apps.api.openapi) - what makes
        # Swagger UI's "Authorize" button understand the `Authorization:
        # Bearer <key>` scheme apps.api.auth.ApiKeyAuthentication
        # actually expects, rather than showing every operation as
        # unauthenticated or guessing wrong.
        self.assertIn("ApiKeyAuth", body)
        self.assertIn("bearer", body)
        # The docs views opt out of NamespaceVersioning and name v1
        # themselves (apps.api.views_docs.SchemaView) - without that the
        # schema came back with no operations at all.
        self.assertIn("/api/v1/sets/", body)


class StretchingEndpointTests(APITestCase):
    def setUp(self):
        from apps.stretching.models import Stretch, StretchRoutine

        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)
        self.quad = Stretch.objects.get(name="Standing Quad Stretch")
        self.system_routine = StretchRoutine.objects.get(name="Lower Body Cool-Down")
        self.bobs_routine = StretchRoutine.objects.create(owner=self.bob, name="Bob's")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}

    def test_lists_system_stretches_and_routines_with_items(self):
        stretches = self.client.get(reverse("api:stretch-list"), **self._auth())
        self.assertEqual(stretches.status_code, 200)
        routine = self.client.get(
            reverse("api:stretch-routine-detail", args=[self.system_routine.pk]), **self._auth()
        )
        self.assertEqual(len(routine.data["items"]), self.system_routine.items.count())

    def test_copying_a_routine(self):
        # Issue #32: one atomic request instead of rebuilding item by item.
        response = self.client.post(
            reverse("api:stretch-routine-copy", args=[self.system_routine.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["owner"], self.alice.pk)
        self.assertNotEqual(response.data["id"], self.system_routine.pk)
        self.assertEqual(len(response.data["items"]), self.system_routine.items.count())
        hidden = self.client.post(
            reverse("api:stretch-routine-copy", args=[self.bobs_routine.pk]), **self._auth()
        )
        self.assertEqual(hidden.status_code, 404)

    def test_system_routine_is_read_only(self):
        response = self.client.patch(
            reverse("api:stretch-routine-detail", args=[self.system_routine.pk]),
            {"name": "Hijacked"}, format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 404)

    def test_another_users_routine_is_invisible(self):
        response = self.client.get(
            reverse("api:stretch-routine-detail", args=[self.bobs_routine.pk]), **self._auth()
        )
        self.assertEqual(response.status_code, 404)

    def test_create_own_routine_and_add_an_item(self):
        routine = self.client.post(
            reverse("api:stretch-routine-list"), {"name": "Evening"},
            format="json", **self._auth(),
        )
        self.assertEqual(routine.status_code, 201)
        item = self.client.post(
            reverse("api:stretch-routine-item-list"),
            {"routine": routine.data["id"], "stretch": self.quad.pk, "hold_seconds": 45},
            format="json", **self._auth(),
        )
        self.assertEqual(item.status_code, 201)

    def test_cannot_add_an_item_to_someone_elses_routine(self):
        response = self.client.post(
            reverse("api:stretch-routine-item-list"),
            {"routine": self.bobs_routine.pk, "stretch": self.quad.pk, "hold_seconds": 45},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_creating_a_session_is_a_quick_log(self):
        response = self.client.post(
            reverse("api:stretch-session-list"),
            {"date": "2026-09-01", "duration": "00:15:00", "routine": self.system_routine.pk},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["name"], "Lower Body Cool-Down")

    def _finished_workout(self, user=None):
        session = workout_services.start_session(user or self.alice, workout=None)
        workout_services.complete_session(session)
        return session

    def test_guided_session_start_mark_complete(self):
        # Issue #34: a client's own player records each stretch.
        start = self.client.post(
            reverse("api:stretch-session-start"),
            {"routine": self.system_routine.pk}, format="json", **self._auth(),
        )
        self.assertEqual(start.status_code, 201, start.data)
        self.assertEqual(start.data["status"], "in_progress")
        performed = start.data["performed_stretches"]
        self.assertEqual(len(performed), self.system_routine.items.count())

        again = self.client.post(
            reverse("api:stretch-session-start"),
            {"routine": self.system_routine.pk}, format="json", **self._auth(),
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.data["id"], start.data["id"])

        session_id = start.data["id"]
        done = self.client.post(
            reverse("api:stretch-session-mark", args=[session_id]),
            {"performed_stretch": performed[0]["id"], "done": True, "actual_seconds": 28},
            format="json", **self._auth(),
        )
        self.assertEqual(done.status_code, 200, done.data)
        self.assertEqual(done.data["status"], "done")
        self.assertEqual(done.data["actual_seconds"], 28)
        if len(performed) > 1:
            skipped = self.client.post(
                reverse("api:stretch-session-mark", args=[session_id]),
                {"performed_stretch": performed[1]["id"], "done": False},
                format="json", **self._auth(),
            )
            self.assertEqual(skipped.data["status"], "skipped")

        complete = self.client.post(
            reverse("api:stretch-session-complete", args=[session_id]), **self._auth()
        )
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.data["status"], "completed")
        self.assertIsNotNone(complete.data["duration"])
        self.assertEqual(
            self.client.post(
                reverse("api:stretch-session-abandon", args=[session_id]), **self._auth()
            ).status_code,
            409,
        )

    def test_guided_cooldown_from_stretches_after_a_workout(self):
        workout = self._finished_workout()
        start = self.client.post(
            reverse("api:stretch-session-start"),
            {"stretches": [self.quad.pk], "name": "Cool-down", "after_workout": workout.pk},
            format="json", **self._auth(),
        )
        self.assertEqual(start.status_code, 201, start.data)
        self.assertEqual(start.data["after_workout"], workout.pk)
        self.assertEqual(start.data["name"], "Cool-down")
        abandon = self.client.post(
            reverse("api:stretch-session-abandon", args=[start.data["id"]]), **self._auth()
        )
        self.assertEqual(abandon.data["status"], "abandoned")

    def test_start_needs_exactly_one_plan_and_own_workout(self):
        url = reverse("api:stretch-session-start")
        self.assertEqual(self.client.post(url, {}, format="json", **self._auth()).status_code, 400)
        both = {"routine": self.system_routine.pk, "stretches": [self.quad.pk]}
        self.assertEqual(
            self.client.post(url, both, format="json", **self._auth()).status_code, 400
        )
        bobs_workout = self._finished_workout(self.bob)
        self.assertEqual(
            self.client.post(
                url, {"stretches": [self.quad.pk], "after_workout": bobs_workout.pk},
                format="json", **self._auth(),
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                url, {"routine": self.bobs_routine.pk}, format="json", **self._auth()
            ).status_code,
            400,
        )

    def test_quick_log_after_a_workout(self):
        workout = self._finished_workout()
        response = self.client.post(
            reverse("api:stretch-session-list"),
            {"duration": "00:10:00", "after_workout": workout.pk},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["after_workout"], workout.pk)
        bobs = self._finished_workout(self.bob)
        rejected = self.client.post(
            reverse("api:stretch-session-list"),
            {"duration": "00:10:00", "after_workout": bobs.pk},
            format="json", **self._auth(),
        )
        self.assertEqual(rejected.status_code, 400)

    def test_session_routine_must_be_visible(self):
        response = self.client.post(
            reverse("api:stretch-session-list"),
            {"date": "2026-09-01", "duration": "00:15:00", "routine": self.bobs_routine.pk},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_sessions_are_scoped_to_the_key_owner(self):
        from datetime import date, timedelta

        from apps.stretching import services as stretching_services

        stretching_services.quick_log(
            self.bob, date=date(2026, 9, 1), duration=timedelta(minutes=5)
        )
        response = self.client.get(reverse("api:stretch-session-list"), **self._auth())
        self.assertEqual(response.data["count"], 0)

    def test_key_without_stretching_permission_gets_403(self):
        _key, secret = _create_key(
            self.alice,
            stretching={
                "can_create": False, "can_read": False, "can_update": False, "can_delete": False,
            },
        )
        response = self.client.get(
            reverse("api:stretch-list"), HTTP_AUTHORIZATION=f"Bearer {secret}"
        )
        self.assertEqual(response.status_code, 403)


class ShoppingListEndpointTests(APITestCase):
    """diet-plans/<id>/shopping-list/ and shopping-settings/
    (apps.nutrition.shopping, docs/API.md)."""

    def setUp(self):
        from decimal import Decimal

        from apps.nutrition.models import (
            DietPlan,
            DietPlanMeal,
            Food,
            MealSlot,
            Recipe,
            RecipeIngredient,
            ServingUnit,
        )

        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _key, self.raw_secret = _create_key(self.alice)
        chicken = Food.objects.create(
            owner=self.alice, name="Chicken", serving_size=Decimal("100"),
            serving_unit=ServingUnit.GRAM, calories=165, protein_grams=Decimal("31"),
            carbohydrate_grams=Decimal("0"), fat_grams=Decimal("3.6"),
        )
        egg = Food.objects.create(
            owner=self.alice, name="Egg", serving_size=Decimal("1"),
            serving_unit=ServingUnit.PIECE, calories=70, protein_grams=Decimal("6"),
            carbohydrate_grams=Decimal("0"), fat_grams=Decimal("5"),
        )
        omelette = Recipe.objects.create(owner=self.alice, name="Omelette", servings=2)
        RecipeIngredient.objects.create(recipe=omelette, food=egg, quantity=Decimal("4"))
        self.plan = DietPlan.objects.create(
            user=self.alice, name="Week", target_calories=2000,
            target_protein_grams=Decimal("150"), target_carbohydrate_grams=Decimal("200"),
            target_fat_grams=Decimal("60"), is_weekly=True,
        )
        lunch = MealSlot.objects.get(name="Lunch", owner=None)
        for day in range(7):
            meal = DietPlanMeal.objects.create(
                diet_plan=self.plan, meal_slot=lunch, target_calories=600, weekday=day
            )
            meal.items.create(food=chicken, quantity=Decimal("300"))
            if day == 1:
                meal.items.create(recipe=omelette, quantity=Decimal("1"))
        self.chicken, self.egg = chicken, egg

    def _auth(self, secret=None):
        return {"HTTP_AUTHORIZATION": f"Bearer {secret or self.raw_secret}"}

    def _url(self, name):
        return reverse(f"api:diet-plan-{name}", args=[self.plan.pk])

    def test_no_shopping_days_is_one_list_for_the_week(self):
        response = self.client.get(self._url("shopping-list"), **self._auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["trip"], {"weekday": None, "days": [0, 1, 2, 3, 4, 5, 6]})
        items = {item["food_name"]: item for item in response.data["items"]}
        self.assertEqual(items["Chicken"]["total"], "2100.00")
        self.assertEqual(items["Chicken"]["unit"], "g")
        self.assertEqual(len(items["Chicken"]["by_day"]), 7)
        self.assertEqual(items["Egg"]["total"], "2.00")  # recipe broken down
        self.assertEqual(items["Egg"]["by_day"], [{"weekday": 1, "quantity": "2.00"}])

    def test_settings_round_trip_and_shape_the_trips(self):
        response = self.client.put(
            self._url("shopping-settings"),
            {"weekdays": [3, 0, 3], "include_shopping_day": True},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"weekdays": [0, 3], "include_shopping_day": True})
        self.assertEqual(
            self.client.get(self._url("shopping-settings"), **self._auth()).data,
            {"weekdays": [0, 3], "include_shopping_day": True},
        )
        response = self.client.get(self._url("shopping-list"), {"trip": 0}, **self._auth())
        self.assertEqual(response.data["trip"], {"weekday": 0, "days": [0, 1, 2]})
        self.assertEqual([t["weekday"] for t in response.data["trips"]], [0, 3])
        chicken = next(i for i in response.data["items"] if i["food"] == self.chicken.pk)
        self.assertEqual(chicken["total"], "900.00")
        self.assertEqual([d["weekday"] for d in chicken["by_day"]], [0, 1, 2])

    def test_patch_changes_only_what_is_given(self):
        self.client.put(
            self._url("shopping-settings"),
            {"weekdays": [0, 3], "include_shopping_day": True}, format="json", **self._auth(),
        )
        response = self.client.patch(
            self._url("shopping-settings"), {"include_shopping_day": False},
            format="json", **self._auth(),
        )
        self.assertEqual(response.data, {"weekdays": [0, 3], "include_shopping_day": False})
        response = self.client.get(self._url("shopping-list"), {"trip": 0}, **self._auth())
        self.assertEqual(response.data["trip"]["days"], [1, 2, 3])

    def test_bad_input_is_a_400(self):
        response = self.client.put(
            self._url("shopping-settings"), {"weekdays": [7], "include_shopping_day": True},
            format="json", **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.client.put(
            self._url("shopping-settings"), {"weekdays": [0], "include_shopping_day": True},
            format="json", **self._auth(),
        )
        for trip in ("monday", "4"):  # not a number; not one of the shopping days
            response = self.client.get(self._url("shopping-list"), {"trip": trip}, **self._auth())
            self.assertEqual(response.status_code, 400, trip)

    def test_another_users_plan_is_a_404(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _key, bob_secret = _create_key(bob)
        for name in ("shopping-list", "shopping-settings"):
            response = self.client.get(self._url(name), **self._auth(bob_secret))
            self.assertEqual(response.status_code, 404, name)

    def test_changing_settings_needs_update_permission(self):
        read_only = {
            "can_create": False, "can_read": True, "can_update": False, "can_delete": False,
        }
        _key, secret = _create_key(self.alice, nutrition=read_only)
        self.assertEqual(
            self.client.get(self._url("shopping-list"), **self._auth(secret)).status_code, 200
        )
        response = self.client.patch(
            self._url("shopping-settings"), {"weekdays": [1]}, format="json", **self._auth(secret)
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.plan.shopping_days.exists())


class ApiVersioningTests(APITestCase):
    """docs/API.md "Versioning" - the version is part of the URL, every
    API view knows it, and the committed contract matches the code."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)

    def test_urls_are_under_the_version(self):
        self.assertTrue(reverse("api:exercise-list").startswith("/api/v1/"))

    def test_views_know_their_version(self):
        response = self.client.get(
            reverse("api:profile"), HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.renderer_context["request"].version, "v1")

    def test_an_unknown_version_is_a_404(self):
        response = self.client.get(
            "/api/v2/profile/", HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        )
        self.assertEqual(response.status_code, 404)

    def test_every_served_version_has_a_committed_schema(self):
        from django.conf import settings

        from apps.api import schema

        self.assertEqual(schema.VERSIONS, settings.REST_FRAMEWORK["ALLOWED_VERSIONS"])

    def test_the_committed_schema_matches_the_code(self):
        # Fails whenever what a client sees changes - run
        # `python manage.py api_schema`, then review the diff of
        # docs/api/openapi-v1.yaml: in v1 only additions are allowed.
        from django.core.management import call_command

        call_command("api_schema", "--check", stdout=io.StringIO())

    def test_ids_in_urls_are_integers(self):
        response = self.client.get(
            "/api/v1/exercises/abc/", HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        )
        self.assertEqual(response.status_code, 404)


class ListFilterTests(APITestCase):
    """Issues #26, #28, #29 - list filters so a client doesn't page
    through everything it ever logged to find one day or one type."""

    def setUp(self):
        from datetime import date

        from apps.activities.models import Activity, ActivityType
        from apps.measurements.models import BodyMeasurement, MeasurementType
        from apps.nutrition.models import DiaryEntry, Food, MealSlot, ServingUnit

        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)
        self.weight = MeasurementType.objects.get(name="Body weight", owner=None)
        self.chest = MeasurementType.objects.get(name="Chest", owner=None)
        aware = lambda day: timezone.make_aware(timezone.datetime(2026, 10, day, 8))  # noqa: E731
        self.w1 = BodyMeasurement.objects.create(
            user=self.alice, measurement_type=self.weight, value=80, recorded_at=aware(1)
        )
        self.w2 = BodyMeasurement.objects.create(
            user=self.alice, measurement_type=self.weight, value=79, recorded_at=aware(10)
        )
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=self.chest, value=100, recorded_at=aware(10)
        )
        self.cycling = ActivityType.objects.get(name="Cycling", owner=None)
        self.hiking = ActivityType.objects.get(name="Hiking", owner=None)
        self.a1 = Activity.objects.create(
            user=self.alice, activity_type=self.cycling, date=date(2026, 10, 1),
            duration=timedelta(minutes=30),
        )
        self.a2 = Activity.objects.create(
            user=self.alice, activity_type=self.cycling, date=date(2026, 10, 10),
            duration=timedelta(minutes=30),
        )
        Activity.objects.create(
            user=self.alice, activity_type=self.hiking, date=date(2026, 10, 10),
            duration=timedelta(minutes=60),
        )
        self.oats = Food.objects.create(
            owner=self.alice, name="Oats", brand="Elovena", serving_size=Decimal("100"),
            serving_unit=ServingUnit.GRAM, calories=370, protein_grams=Decimal("13"),
            carbohydrate_grams=Decimal("60"), fat_grams=Decimal("7"), off_id="6408430000000",
        )
        self.rice = Food.objects.create(
            owner=self.alice, name="Rice", serving_size=Decimal("100"),
            serving_unit=ServingUnit.GRAM, calories=130, protein_grams=Decimal("2.7"),
            carbohydrate_grams=Decimal("28"), fat_grams=Decimal("0.3"),
        )
        breakfast = MealSlot.objects.get(name="Breakfast", owner=None)
        self.entries = {
            day: DiaryEntry.objects.create(
                user=self.alice, date=date(2026, 10, day), meal_slot=breakfast,
                food=self.oats, quantity=Decimal("50"),
            )
            for day in (1, 2, 3)
        }

    def _ids(self, name, query):
        response = self.client.get(
            reverse(name), query, HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        )
        self.assertEqual(response.status_code, 200, response.data)
        return {row["id"] for row in response.data["results"]}

    def _status(self, name, query):
        return self.client.get(
            reverse(name), query, HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        ).status_code

    def test_diary_entries_by_date(self):
        self.assertEqual(
            self._ids("api:diary-entry-list", {"date": "2026-10-02"}), {self.entries[2].pk}
        )
        self.assertEqual(
            self._ids(
                "api:diary-entry-list", {"date_after": "2026-10-02", "date_before": "2026-10-03"}
            ),
            {self.entries[2].pk},
        )
        self.assertEqual(self._status("api:diary-entry-list", {"date": "2026-13-01"}), 400)

    def test_measurements_by_type_and_time(self):
        self.assertEqual(
            self._ids("api:measurement-list", {"measurement_type": self.weight.pk}),
            {self.w1.pk, self.w2.pk},
        )
        self.assertEqual(
            self._ids(
                "api:measurement-list",
                {"measurement_type": self.weight.pk, "recorded_after": "2026-10-05"},
            ),
            {self.w2.pk},
        )
        self.assertEqual(
            self._ids(
                "api:measurement-list",
                {"measurement_type": self.weight.pk, "recorded_before": "2026-10-05"},
            ),
            {self.w1.pk},
        )
        self.assertEqual(self._status("api:measurement-list", {"measurement_type": "x"}), 400)

    def test_activities_by_type_and_date(self):
        self.assertEqual(
            self._ids("api:activity-list", {"activity_type": self.cycling.pk}),
            {self.a1.pk, self.a2.pk},
        )
        self.assertEqual(
            self._ids(
                "api:activity-list",
                {"activity_type": self.cycling.pk, "date_after": "2026-10-05"},
            ),
            {self.a2.pk},
        )
        self.assertEqual(self._status("api:activity-list", {"date_before": "nope"}), 400)

    def test_food_search_matches_name_brand_and_barcode(self):
        # Shared seeded foods may match too - only ours are checked.
        for query in ("oat", "elovena"):
            ids = self._ids("api:food-list", {"search": query})
            self.assertIn(self.oats.pk, ids)
            self.assertNotIn(self.rice.pk, ids)
        self.assertEqual(self._ids("api:food-list", {"search": "6408430000000"}), {self.oats.pk})
        self.assertEqual(
            self._ids("api:food-list", {"ids": f"{self.rice.pk},{self.oats.pk}"}),
            {self.oats.pk, self.rice.pk},
        )
        self.assertEqual(self._status("api:food-list", {"ids": "1,a"}), 400)


class OpenFoodFactsEndpointTests(APITestCase):
    """Issue #41 - importing a food by barcode goes through the same
    service as the web, so it keeps its Nutri-Score, NOVA and OFF link
    and doesn't duplicate an already-imported product."""

    def setUp(self):
        from apps.nutrition.models import OpenFoodFactsSettings
        from apps.nutrition.tests import RAW_OFF_PRODUCT

        OpenFoodFactsSettings.objects.all().delete()  # enabled by default
        self.raw = RAW_OFF_PRODUCT
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)

    def _import(self, barcode="1234567890123"):
        return self.client.post(
            reverse("api:food-import-off"), {"barcode": barcode}, format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}",
        )

    def test_import_creates_once_then_returns_the_existing_food(self):
        from unittest import mock

        from apps.nutrition import openfoodfacts
        from apps.nutrition.models import Food

        with mock.patch.object(openfoodfacts, "get_product", return_value=self.raw):
            first = self._import()
            second = self._import()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.data["off_id"], "1234567890123")
        self.assertIsNone(first.data["owner"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["id"], first.data["id"])
        self.assertEqual(Food.objects.filter(off_id="1234567890123").count(), 1)

    def test_unknown_barcode_is_404_and_a_bad_one_400(self):
        from unittest import mock

        from apps.nutrition import openfoodfacts

        with mock.patch.object(openfoodfacts, "get_product", return_value=None):
            self.assertEqual(self._import().status_code, 404)
        self.assertEqual(self._import("12ab").status_code, 400)

    def test_rate_limited_without_a_local_copy_is_429(self):
        from unittest import mock

        from apps.nutrition import openfoodfacts

        with mock.patch.object(
            openfoodfacts, "get_product",
            side_effect=openfoodfacts.OpenFoodFactsRateLimited("busy"),
        ):
            self.assertEqual(self._import().status_code, 429)

    def test_disabled_integration_is_503(self):
        from apps.nutrition.models import OpenFoodFactsSettings

        OpenFoodFactsSettings.objects.create(pk=1, enabled=False)
        self.assertEqual(self._import().status_code, 503)

    def test_search_returns_local_foods_and_off_products(self):
        from unittest import mock

        from apps.nutrition import openfoodfacts

        with mock.patch.object(openfoodfacts, "search_products", return_value=[self.raw]):
            response = self.client.get(
                reverse("api:food-search-off"), {"q": "muesli"},
                HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}",
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "ok")
        self.assertEqual(response.data["results"][0]["off_id"], "1234567890123")
        self.assertEqual(response.data["results"][0]["name"], "Test Muesli")


class NutritionGoalEndpointTests(APITestCase):
    """Issue #33 - nutrition onboarding and goal changes through the
    API go through the same services as the web."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}

    def _post_goal(self, **overrides):
        payload = {"goal_type": "fat_loss_moderate", "target_rate_kg_per_week": "-0.5"}
        payload.update(overrides)
        return self.client.post(
            reverse("api:nutrition-goal-list"), payload, format="json", **self.auth
        )

    def _onboard(self):
        from apps.measurements.models import BodyMeasurement, MeasurementType

        profile = self.client.put(
            reverse("api:nutrition-profile"),
            {
                "biological_sex": "male", "birth_date": "1990-01-01",
                "activity_job": "sedentary", "activity_level": "moderate",
            },
            format="json", **self.auth,
        )
        self.assertEqual(profile.status_code, 201, profile.data)
        self.client.patch(reverse("api:profile"), {"height": "1.80"}, format="json", **self.auth)
        BodyMeasurement.objects.create(
            user=self.alice,
            measurement_type=MeasurementType.objects.get(name="Body weight", owner=None),
            value=Decimal("90"),
        )

    def test_onboarding_then_a_goal_with_a_calculated_target(self):
        from apps.nutrition.models import NutritionGoal, NutritionTarget

        self.assertEqual(self._post_goal().status_code, 400)  # no profile yet
        self._onboard()
        response = self._post_goal()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["goal_type"], "fat_loss_moderate")
        self.assertEqual(response.data["target"]["source"], "calculated")
        self.assertGreater(response.data["target"]["daily_calories"], 1000)

        second = self._post_goal(goal_type="maintenance", target_rate_kg_per_week="0")
        self.assertEqual(second.status_code, 201)
        # The first goal and target stay in history, ended.
        self.assertEqual(NutritionGoal.objects.filter(user=self.alice).count(), 2)
        self.assertEqual(
            NutritionTarget.objects.filter(user=self.alice, ended_at__isnull=True).count(), 1
        )
        self.assertIsNotNone(NutritionGoal.objects.get(pk=response.data["id"]).ended_at)

    def test_rate_must_match_the_goal(self):
        self._onboard()
        response = self._post_goal(target_rate_kg_per_week="0.5")
        self.assertEqual(response.status_code, 400)
        self.assertIn("target_rate_kg_per_week", response.data)

    def test_profile_put_updates_once_it_exists(self):
        self._onboard()
        response = self.client.put(
            reverse("api:nutrition-profile"),
            {
                "biological_sex": "male", "birth_date": "1990-01-01",
                "activity_job": "sedentary", "activity_level": "active",
            },
            format="json", **self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["activity_level"], "active")

    def test_adjustment_suggestion_and_accept(self):
        from unittest import mock

        from apps.nutrition import suggestions

        no_goal = self.client.get(reverse("api:nutrition-adjustment-suggestion"), **self.auth)
        self.assertEqual(no_goal.status_code, 200)
        self.assertEqual(no_goal.data["action"], "no_active_goal")
        refused = self.client.post(
            reverse("api:nutrition-adjustment-suggestion-accept"), **self.auth
        )
        self.assertEqual(refused.status_code, 409)

        self._onboard()
        self._post_goal()
        adjust = suggestions.AdjustmentSuggestion(
            action=suggestions.AdjustmentAction.ADJUST,
            target_rate_kg_per_week=Decimal("-0.5"),
            actual_rate_kg_per_week=Decimal("-0.1"),
            suggested_daily_calories=1900,
            delta_kcal=-150,
            confidence=suggestions.Confidence.HIGH,
            reason="Losing slower than planned.",
        )
        with mock.patch.object(suggestions, "suggest_calorie_adjustment", return_value=adjust):
            accepted = self.client.post(
                reverse("api:nutrition-adjustment-suggestion-accept"), **self.auth
            )
        self.assertEqual(accepted.status_code, 201, accepted.data)
        self.assertEqual(accepted.data["daily_calories"], 1900)
        self.assertEqual(accepted.data["source"], "adjusted")


class TrainingAnalyticsEndpointTests(APITestCase):
    """Issue #35 - the web Analytics page's charts as data, canonical kg."""

    def setUp(self):
        from apps.exercises.models import MuscleGroup

        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.alice.unit_system = "imperial"  # must not change anything here
        self.alice.save()
        _api_key, self.raw_secret = _create_key(self.alice)
        self.exercise = Exercise.objects.create(name="API Analytics Squat", owner=None)
        self.legs = MuscleGroup.objects.first()
        self.exercise.primary_muscle_groups.add(self.legs)
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        workout_services.log_set(performed, weight=Decimal("100"), reps=5)
        workout_services.log_set(performed, weight=Decimal("110"), reps=3)
        workout_services.log_set(performed, weight=Decimal("40"), reps=10, is_warmup=True)
        workout_services.complete_session(session)

    def _get(self, name, *args, **query):
        return self.client.get(
            reverse(name, args=args), query, HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}"
        )

    def test_weekly_volume(self):
        response = self._get("api:analytics-volume", range="7d")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["range"]["range"], "7d")
        self.assertEqual(len(response.data["weeks"]), 1)
        self.assertEqual(Decimal(response.data["weeks"][0]["volume"]), Decimal("830"))

    def test_muscle_group_volume(self):
        response = self._get("api:analytics-muscle-groups")
        self.assertEqual(response.status_code, 200)
        row = response.data["muscle_groups"][0]
        self.assertEqual(row["muscle_group"], self.legs.pk)
        self.assertEqual(Decimal(row["volume"]), Decimal("830"))

    def test_exercise_progress(self):
        response = self._get("api:analytics-exercise", self.exercise.pk, range="all")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["range"]["start"], None)
        self.assertEqual(response.data["session_count"], 1)
        self.assertEqual(Decimal(response.data["total_volume"]), Decimal("830"))
        best = response.data["sessions"][0]
        self.assertEqual(Decimal(best["weight"]), Decimal("110"))
        self.assertEqual(best["reps"], 3)
        self.assertGreater(Decimal(best["estimated_one_rep_max"]), Decimal("110"))

    def test_hidden_exercise_is_404(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        private = Exercise.objects.create(name="Bob's lift", owner=bob)
        self.assertEqual(self._get("api:analytics-exercise", private.pk).status_code, 404)


class DisplayNameTests(APITestCase):
    """Issue #36 - built-in names translated like the web shows them."""

    def setUp(self):
        from apps.nutrition.models import MealSlot

        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)
        self.breakfast = MealSlot.objects.get(name="Breakfast", owner=None)

    def _get(self, url, **headers):
        return self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.raw_secret}", **headers)

    def test_profile_language_by_default(self):
        url = reverse("api:meal-slot-detail", args=[self.breakfast.pk])
        self.assertEqual(self._get(url).data["display_name"], "Breakfast")
        self.alice.language = "fi"
        self.alice.save(update_fields=["language"])
        data = self._get(url).data
        self.assertEqual(data["name"], "Breakfast")
        self.assertEqual(data["display_name"], "Aamiainen")

    def test_accept_language_wins_and_unsupported_falls_back(self):
        url = reverse("api:meal-slot-detail", args=[self.breakfast.pk])
        self.assertEqual(
            self._get(url, HTTP_ACCEPT_LANGUAGE="fi-FI,fi;q=0.9").data["display_name"],
            "Aamiainen",
        )
        self.alice.language = "fi"
        self.alice.save(update_fields=["language"])
        self.assertEqual(
            self._get(url, HTTP_ACCEPT_LANGUAGE="xx").data["display_name"], "Aamiainen"
        )
        self.assertEqual(
            self._get(url, HTTP_ACCEPT_LANGUAGE="en").data["display_name"], "Breakfast"
        )

    def test_nested_and_custom_names(self):
        from apps.stretching.models import StretchRoutine

        routine = StretchRoutine.objects.get(name="Lower Body Cool-Down")
        mine = StretchRoutine.objects.create(owner=self.alice, name="Iltavenyttely")
        fi = {"HTTP_ACCEPT_LANGUAGE": "fi"}
        self.assertEqual(
            self._get(reverse("api:stretch-routine-detail", args=[routine.pk]), **fi).data[
                "display_name"
            ],
            "Jäähdyttely: alavartalo",
        )
        self.assertEqual(
            self._get(reverse("api:stretch-routine-detail", args=[mine.pk]), **fi).data[
                "display_name"
            ],
            "Iltavenyttely",
        )


class TutorialEndpointTests(APITestCase):
    """Issue #37 - tours and their shared completion state."""

    def setUp(self):
        from apps.tutorials.tours import TOURS

        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _api_key, self.raw_secret = _create_key(self.alice)
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_secret}"}
        self.tour = TOURS[0]

    def test_list_completes_and_resets(self):
        from apps.tutorials.models import TutorialCompletion

        response = self.client.get(reverse("api:tutorial-list"), **self.auth)
        self.assertEqual(response.status_code, 200)
        first = response.data[0]
        self.assertEqual(first["key"], self.tour.key)
        self.assertFalse(first["completed"])
        self.assertTrue(first["steps"])

        url = reverse("api:tutorial-complete", args=[self.tour.key])
        self.assertEqual(self.client.post(url, **self.auth).status_code, 204)
        self.assertTrue(
            TutorialCompletion.objects.filter(user=self.alice, tour=self.tour.key).exists()
        )
        listed = self.client.get(reverse("api:tutorial-list"), **self.auth).data
        self.assertTrue(listed[0]["completed"])

        reset = reverse("api:tutorial-reset", args=[self.tour.key])
        self.assertEqual(self.client.post(reset, **self.auth).status_code, 204)
        self.assertFalse(TutorialCompletion.objects.filter(user=self.alice).exists())
        self.client.post(url, **self.auth)
        self.client.post(reverse("api:tutorial-reset-all"), **self.auth)
        self.assertFalse(TutorialCompletion.objects.filter(user=self.alice).exists())

    def test_unknown_tour_is_404(self):
        url = reverse("api:tutorial-complete", args=["nope"])
        self.assertEqual(self.client.post(url, **self.auth).status_code, 404)

    def test_texts_in_the_request_language(self):
        english = self.client.get(reverse("api:tutorial-list"), **self.auth).data[0]["title"]
        finnish = self.client.get(
            reverse("api:tutorial-list"), HTTP_ACCEPT_LANGUAGE="fi", **self.auth
        ).data[0]["title"]
        self.assertNotEqual(english, finnish)

    def test_tutorials_enabled_on_the_profile(self):
        response = self.client.patch(
            reverse("api:profile"), {"tutorials_enabled": False}, format="json", **self.auth
        )
        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.tutorials_enabled)


class SocialEndpointTests(APITestCase):
    """Issue #37 - friends, messages and groups through
    apps.social.services, with the same rules as the web."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.carol = User.objects.create_user(username="carol", password="s3cret-pass")
        _key, alice_secret = _create_key(self.alice)
        _key, bob_secret = _create_key(self.bob)
        self.as_alice = {"HTTP_AUTHORIZATION": f"Bearer {alice_secret}"}
        self.as_bob = {"HTTP_AUTHORIZATION": f"Bearer {bob_secret}"}

    def _befriend(self):
        from apps.social import services as social_services

        request = social_services.send_friend_request(self.alice, self.bob)
        social_services.accept_friend_request(request, acting_user=self.bob)

    def test_friend_request_flow(self):
        search = self.client.get(reverse("api:social-users"), {"search": "bo"}, **self.as_alice)
        self.assertEqual([u["username"] for u in search.data], ["bob"])
        sent = self.client.post(
            reverse("api:social-friend-requests"), {"user": self.bob.pk},
            format="json", **self.as_alice,
        )
        self.assertEqual(sent.status_code, 201, sent.data)
        again = self.client.post(
            reverse("api:social-friend-requests"), {"user": self.bob.pk},
            format="json", **self.as_alice,
        )
        self.assertEqual(again.status_code, 400)
        self.assertIn("detail", again.data)
        # Only the recipient can answer.
        self.assertEqual(
            self.client.post(
                reverse("api:social-friend-request-accept", args=[sent.data["id"]]),
                **self.as_alice,
            ).status_code,
            404,
        )
        incoming = self.client.get(reverse("api:social-friend-requests"), **self.as_bob)
        self.assertEqual(incoming.data[0]["from_user"]["username"], "alice")
        accepted = self.client.post(
            reverse("api:social-friend-request-accept", args=[sent.data["id"]]), **self.as_bob
        )
        self.assertEqual(accepted.data["status"], "accepted")
        friends = self.client.get(reverse("api:social-friends"), **self.as_alice)
        self.assertEqual(friends.data, [{"id": self.bob.pk, "username": "bob", "muted": False}])
        self.client.post(reverse("api:social-friend-mute", args=[self.bob.pk]), **self.as_alice)
        friends = self.client.get(reverse("api:social-friends"), **self.as_alice)
        self.assertTrue(friends.data[0]["muted"])
        removed = self.client.delete(
            reverse("api:social-friend", args=[self.bob.pk]), **self.as_alice
        )
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(self.client.get(reverse("api:social-friends"), **self.as_alice).data, [])

    def test_private_setting_is_respected_in_the_users_language(self):
        self.bob.allow_friend_requests = False
        self.bob.save()
        self.alice.language = "fi"
        self.alice.save()
        response = self.client.post(
            reverse("api:social-friend-requests"), {"user": self.bob.pk},
            format="json", **self.as_alice,
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotEqual(response.data["detail"], "This user isn't accepting friend requests.")

    def test_direct_messages_only_between_friends(self):
        url = reverse("api:social-messages", args=[self.bob.pk])
        self.assertEqual(self.client.get(url, **self.as_alice).status_code, 404)
        self._befriend()
        sent = self.client.post(url, {"body": "Hi"}, format="json", **self.as_alice)
        self.assertEqual(sent.status_code, 201)
        conversations = self.client.get(reverse("api:social-conversations"), **self.as_bob)
        self.assertEqual(conversations.data["friends"][0]["unread"], 1)
        thread = self.client.get(
            reverse("api:social-messages", args=[self.alice.pk]), **self.as_bob
        )
        self.assertEqual([m["body"] for m in thread.data], ["Hi"])
        conversations = self.client.get(reverse("api:social-conversations"), **self.as_bob)
        self.assertEqual(conversations.data["friends"][0]["unread"], 0)
        newer = self.client.get(url, {"after": sent.data["id"]}, **self.as_alice)
        self.assertEqual(newer.data, [])

    def test_blocking_ends_the_friendship(self):
        self._befriend()
        blocked = self.client.post(
            reverse("api:social-blocks"), {"user": self.bob.pk}, format="json", **self.as_alice
        )
        self.assertEqual(blocked.status_code, 204)
        self.assertEqual(self.client.get(reverse("api:social-friends"), **self.as_alice).data, [])
        self.assertEqual(
            self.client.get(reverse("api:social-blocks"), **self.as_alice).data[0]["id"],
            self.bob.pk,
        )
        search = self.client.get(reverse("api:social-users"), {"search": "bo"}, **self.as_alice)
        self.assertEqual(search.data, [])
        unblock = self.client.delete(
            reverse("api:social-block", args=[self.bob.pk]), **self.as_alice
        )
        self.assertEqual(unblock.status_code, 204)

    def test_group_lifecycle(self):
        self._befriend()
        created = self.client.post(
            reverse("api:social-groups"), {"name": "Lifters"}, format="json", **self.as_alice
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["role"], "owner")
        group_id = created.data["id"]
        # Not a member: invisible.
        group_url = reverse("api:social-group", args=[group_id])
        self.assertEqual(self.client.get(group_url, **self.as_bob).status_code, 404)
        invited = self.client.post(
            reverse("api:social-group-invites", args=[group_id]), {"user": self.bob.pk},
            format="json", **self.as_alice,
        )
        self.assertEqual(invited.status_code, 201, invited.data)
        stranger = self.client.post(
            reverse("api:social-group-invites", args=[group_id]), {"user": self.carol.pk},
            format="json", **self.as_alice,
        )
        self.assertEqual(stranger.status_code, 400)  # only friends
        invites = self.client.get(reverse("api:social-group-invite-list"), **self.as_bob)
        self.client.post(
            reverse("api:social-group-invite-accept", args=[invites.data[0]["id"]]), **self.as_bob
        )
        as_member = self.client.get(reverse("api:social-group", args=[group_id]), **self.as_bob)
        self.assertEqual(as_member.data["role"], "member")
        self.assertIsNone(as_member.data["invite_code"])
        self.assertEqual(
            self.client.patch(
                reverse("api:social-group", args=[group_id]), {"name": "Mine"},
                format="json", **self.as_bob,
            ).status_code,
            403,
        )
        message = self.client.post(
            reverse("api:social-group-messages", args=[group_id]), {"body": "Leg day"},
            format="json", **self.as_bob,
        )
        self.assertEqual(message.status_code, 201)
        self.assertEqual(
            self.client.get(reverse("api:social-groups"), **self.as_alice).data[0]["unread"], 1
        )
        members = self.client.get(
            reverse("api:social-group-members", args=[group_id]), **self.as_alice
        )
        self.assertEqual(len(members.data), 2)
        promoted = self.client.patch(
            reverse("api:social-group-member", args=[group_id, self.bob.pk]), {"role": "admin"},
            format="json", **self.as_alice,
        )
        self.assertEqual(promoted.data["role"], "admin")
        leave_url = reverse("api:social-group-leave", args=[group_id])
        owner_leaves = self.client.post(leave_url, **self.as_alice)
        self.assertEqual(owner_leaves.status_code, 400)
        transferred = self.client.post(
            reverse("api:social-group-transfer", args=[group_id]), {"user": self.bob.pk},
            format="json", **self.as_alice,
        )
        self.assertEqual(transferred.data["role"], "admin")
        self.assertEqual(
            self.client.post(leave_url, **self.as_alice).status_code,
            204,
        )
        self.assertEqual(
            self.client.delete(group_url, **self.as_bob).status_code,
            204,
        )

    def test_invite_code_join(self):
        created = self.client.post(
            reverse("api:social-groups"), {"name": "Open"}, format="json", **self.as_alice
        )
        group_id = created.data["id"]
        link = self.client.post(
            reverse("api:social-group-invite-link", args=[group_id]), {"enabled": True},
            format="json", **self.as_alice,
        )
        code = link.data["invite_code"]
        self.assertTrue(link.data["invite_enabled"])
        joined = self.client.post(
            reverse("api:social-group-join"), {"code": code}, format="json", **self.as_bob
        )
        self.assertEqual(joined.status_code, 200, joined.data)
        self.assertEqual(joined.data["member_count"], 2)
        regenerated = self.client.post(
            reverse("api:social-group-invite-link", args=[group_id]), {"regenerate": True},
            format="json", **self.as_alice,
        )
        self.assertNotEqual(regenerated.data["invite_code"], code)
        bad = self.client.post(
            reverse("api:social-group-join"), {"code": code}, format="json", **self.as_bob
        )
        self.assertEqual(bad.status_code, 400)

    def test_key_without_social_permission(self):
        _key, secret = _create_key(
            self.alice,
            social={
                "can_create": False, "can_read": False, "can_update": False, "can_delete": False,
            },
        )
        response = self.client.get(
            reverse("api:social-friends"), HTTP_AUTHORIZATION=f"Bearer {secret}"
        )
        self.assertEqual(response.status_code, 403)

    def test_recently_active(self):
        session = workout_services.start_session(self.alice, workout=None)
        self.assertIsNotNone(session)
        response = self.client.get(reverse("api:analytics-recently-active"), **self.as_bob)
        self.assertEqual(response.status_code, 200)
        alice = next(row for row in response.data if row["username"] == "alice")
        self.assertTrue(alice["is_in_progress"])


class CoachingEndpointTests(APITestCase):
    """Issue #37 - both sides of coaching through apps.coaching.services."""

    def setUp(self):
        self.coach = User.objects.create_user(
            username="coach", password="s3cret-pass", is_personal_trainer=True
        )
        self.client_user = User.objects.create_user(username="client", password="s3cret-pass")
        self.other = User.objects.create_user(username="other", password="s3cret-pass")
        _key, coach_secret = _create_key(self.coach)
        _key, client_secret = _create_key(self.client_user)
        self.as_coach = {"HTTP_AUTHORIZATION": f"Bearer {coach_secret}"}
        self.as_client = {"HTTP_AUTHORIZATION": f"Bearer {client_secret}"}

    def _start_coaching(self):
        sent = self.client.post(
            reverse("api:coaching-requests"), {"username": "coach"},
            format="json", **self.as_client,
        )
        self.assertEqual(sent.status_code, 201, sent.data)
        accepted = self.client.post(
            reverse("api:coaching-request-accept", args=[sent.data["id"]]), **self.as_coach
        )
        self.assertEqual(accepted.data["status"], "accepted")

    def test_request_and_relationship(self):
        not_a_trainer = self.client.post(
            reverse("api:coaching-requests"), {"username": "other"},
            format="json", **self.as_client,
        )
        self.assertEqual(not_a_trainer.status_code, 400)
        self._start_coaching()
        coaches = self.client.get(reverse("api:coaching-coaches"), **self.as_client)
        self.assertEqual(coaches.data[0]["coach"]["username"], "coach")
        clients = self.client.get(reverse("api:coaching-clients"), **self.as_coach)
        self.assertEqual(clients.data[0]["coachee"]["username"], "client")
        detail = self.client.get(
            reverse("api:coaching-client", args=["client"]), **self.as_coach
        )
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["client"]["username"], "client")
        self.assertIn("training_summary", detail.data)
        # Not a client: 404, never which case it is.
        self.assertEqual(
            self.client.get(
                reverse("api:coaching-client", args=["other"]), **self.as_coach
            ).status_code,
            404,
        )
        ended = self.client.post(
            reverse("api:coaching-relationship-end", args=[clients.data[0]["id"]]),
            **self.as_client,
        )
        self.assertIsNotNone(ended.data["ended_at"])
        self.assertEqual(
            self.client.get(
                reverse("api:coaching-client", args=["client"]), **self.as_coach
            ).status_code,
            404,
        )

    def test_share_import_and_update_a_program(self):
        from apps.programs.models import Program, Workout

        self._start_coaching()
        program = Program.objects.create(owner=self.coach, name="Block 1")
        Workout.objects.create(program=program, name="Day A")
        share_url = reverse("api:coaching-program-clients", args=[program.pk])
        self.assertEqual(
            self.client.put(
                share_url, {"clients": [self.other.pk]}, format="json", **self.as_coach
            ).status_code,
            400,
        )
        shared = self.client.put(
            share_url, {"clients": [self.client_user.pk]}, format="json", **self.as_coach
        )
        self.assertEqual(shared.data["clients"], [self.client_user.pk])
        self.assertEqual(
            self.client.get(share_url, **self.as_client).status_code, 403
        )  # not a trainer

        listed = self.client.get(reverse("api:coaching-shared"), **self.as_client)
        self.assertEqual(listed.data["programs"][0]["id"], program.pk)
        self.assertFalse(listed.data["programs"][0]["imported"])
        imported = self.client.post(
            reverse("api:coaching-shared-program-import", args=[program.pk]), **self.as_client
        )
        self.assertEqual(imported.status_code, 201, imported.data)
        copy_id = imported.data["id"]
        update_url = reverse("api:coaching-program-update", args=[copy_id])
        self.assertFalse(self.client.get(update_url, **self.as_client).data["available"])
        self.assertEqual(self.client.post(update_url, **self.as_client).status_code, 409)

        Workout.objects.create(program=program, name="Day B")
        program.bump_version()
        preview = self.client.get(update_url, **self.as_client)
        self.assertTrue(preview.data["available"])
        self.assertIn("Day B", preview.data["diff"])
        applied = self.client.post(update_url, **self.as_client)
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(
            sorted(w["name"] for w in applied.data["workouts"]), ["Day A", "Day B"]
        )

    def test_import_needs_sharing(self):
        from apps.programs.models import Program

        self._start_coaching()
        program = Program.objects.create(owner=self.coach, name="Private")
        response = self.client.post(
            reverse("api:coaching-shared-program-import", args=[program.pk]), **self.as_client
        )
        self.assertEqual(response.status_code, 400)


@override_settings(ASSISTANT_PROVIDER="anthropic", ASSISTANT_API_KEY="sk-ant-shared-key-123456")
class AssistantEndpointTests(APITestCase):
    """Issue #37 - the assistant through apps.assistant.services. Replies
    are only queued here (ASSISTANT_RUN_INLINE is off), no model runs."""

    def setUp(self):
        from apps.assistant.models import AssistantSettings, SharedKeyAccess

        site = AssistantSettings.load()
        site.shared_key_access = SharedKeyAccess.EVERYONE
        site.save()
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        _key, secret = _create_key(self.alice)
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {secret}"}

    def test_off_until_enabled(self):
        status_data = self.client.get(reverse("api:assistant-status"), **self.auth).data
        self.assertFalse(status_data["enabled"])
        self.assertTrue(status_data["available"])
        self.assertEqual(status_data["key_source"], "shared")
        refused = self.client.post(
            reverse("api:assistant-conversations"), {"text": "Hi"}, format="json", **self.auth
        )
        self.assertEqual(refused.status_code, 400)
        enabled = self.client.post(reverse("api:assistant-enable"), **self.auth)
        self.assertTrue(enabled.data["enabled"])
        disabled = self.client.post(reverse("api:assistant-disable"), **self.auth)
        self.assertFalse(disabled.data["enabled"])

    def test_conversation_flow(self):
        self.client.post(reverse("api:assistant-enable"), **self.auth)
        started = self.client.post(
            reverse("api:assistant-conversations"), {"text": "Plan my week"},
            format="json", **self.auth,
        )
        self.assertEqual(started.status_code, 201, started.data)
        self.assertEqual(started.data["title"], "Plan my week")
        user_message, reply = started.data["messages"]
        self.assertEqual(user_message["role"], "user")
        self.assertEqual(reply["status"], "pending")
        busy = self.client.post(
            reverse("api:assistant-conversation-messages", args=[started.data["id"]]),
            {"text": "And?"}, format="json", **self.auth,
        )
        self.assertEqual(busy.status_code, 400)  # the reply is still being written
        polled = self.client.get(reverse("api:assistant-message", args=[reply["id"]]), **self.auth)
        self.assertEqual(polled.data["status"], "pending")
        listed = self.client.get(reverse("api:assistant-conversations"), **self.auth)
        self.assertEqual(listed.data[0]["id"], started.data["id"])
        deleted = self.client.delete(
            reverse("api:assistant-conversation", args=[started.data["id"]]), **self.auth
        )
        self.assertEqual(deleted.status_code, 204)

    def test_another_users_conversation_is_404(self):
        from apps.assistant.models import Conversation

        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        theirs = Conversation.objects.create(
            user=bob, title="x", provider="anthropic", model="m", key_source="shared"
        )
        url = reverse("api:assistant-conversation", args=[theirs.pk])
        self.assertEqual(self.client.get(url, **self.auth).status_code, 404)

    def test_settings(self):
        response = self.client.patch(
            reverse("api:assistant-settings"),
            {"about_me": "Knee injury", "reads_food": False, "own_key_effort": "low"},
            format="json", **self.auth,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["about_me"], "Knee injury")
        self.assertFalse(response.data["reads_food"])
        self.assertTrue(response.data["reads_body"])
        self.assertEqual(response.data["own_key_effort"], "low")
        bad = self.client.patch(
            reverse("api:assistant-settings"), {"own_key_model": "gpt"},
            format="json", **self.auth,
        )
        self.assertEqual(bad.status_code, 400)

    def test_proposals(self):
        from apps.assistant.models import AssistantMessage, AssistantProposal, Conversation

        conversation = Conversation.objects.create(
            user=self.alice, title="x", provider="anthropic", model="m", key_source="shared"
        )
        message = AssistantMessage.objects.create(conversation=conversation, role="assistant")
        broken = AssistantProposal.objects.create(
            message=message, kind="program", title="Plan", payload={}
        )
        accepted = self.client.post(
            reverse("api:assistant-proposal-accept", args=[broken.pk]), **self.auth
        )
        self.assertEqual(accepted.status_code, 409)
        dismissed = self.client.post(
            reverse("api:assistant-proposal-dismiss", args=[broken.pk]), **self.auth
        )
        self.assertEqual(dismissed.data["status"], "dismissed")
