from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class CustomUserModelTests(TestCase):
    def test_user_has_unit_and_timezone_preferences(self):
        user = User.objects.create_user(username="alice", password="s3cret-pass")
        self.assertEqual(user.unit_system, "metric")
        self.assertEqual(user.timezone, "UTC")

    def test_language_defaults_to_english(self):
        user = User.objects.create_user(username="alice", password="s3cret-pass")
        self.assertEqual(user.language, "en")


class LanguagePreferenceTests(TestCase):
    """apps.accounts.middleware.UserLanguageMiddleware — a logged-in
    user's stored `language` (set on the profile page) drives Django's
    gettext-based UI translation, overriding whatever LocaleMiddleware
    would otherwise guess from the session/cookie/Accept-Language header.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_setting_language_on_profile_persists_it(self):
        self.client.post(
            reverse("profile"), {"unit_system": "metric", "timezone": "UTC", "language": "fi"}
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.language, "fi")

    def test_dashboard_renders_in_the_users_chosen_language(self):
        self.alice.language = "fi"
        self.alice.save()
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'aria-label="Koti"')  # "Home" nav link

    def test_dashboard_renders_in_english_by_default(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'aria-label="Home"')

    def test_language_choice_affects_translatable_form_labels_too(self):
        self.alice.language = "fi"
        self.alice.save()
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Aikavyöhyke")  # "Timezone" label


class TimezonePreferenceTests(TestCase):
    """apps.accounts.middleware.UserTimezoneMiddleware — regression: a
    logged-in user's stored `timezone` (set on the profile page) was
    saved and validated but never actually applied anywhere; every
    timezone-aware render silently used settings.TIME_ZONE (UTC)
    regardless of what a user had chosen."""

    def setUp(self):
        from datetime import datetime
        from datetime import timezone as dt_timezone

        from apps.workouts import services as workout_services

        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        self.session = workout_services.start_session(self.alice, workout=None)
        # Deliberately close to UTC midnight so a +14 zone lands on the
        # *next* calendar date — the clearest possible signal that the
        # active timezone, not just the clock face, actually changed.
        self.session.started_at = datetime(2026, 1, 1, 23, 0, tzinfo=dt_timezone.utc)
        self.session.save(update_fields=["started_at"])

    def test_a_logged_datetime_renders_in_utc_by_default(self):
        response = self.client.get(reverse("workouts:session-list"))
        self.assertContains(response, "2026-01-01 23:00")

    def test_a_logged_datetime_renders_in_the_users_chosen_timezone(self):
        self.alice.timezone = "Pacific/Kiritimati"  # UTC+14, no DST
        self.alice.save()
        response = self.client.get(reverse("workouts:session-list"))
        self.assertContains(response, "2026-01-02 13:00")
        self.assertNotContains(response, "2026-01-01 23:00")

    def test_setting_timezone_on_profile_persists_and_takes_effect_immediately(self):
        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "Pacific/Kiritimati", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.timezone, "Pacific/Kiritimati")
        response = self.client.get(reverse("workouts:session-list"))
        self.assertContains(response, "2026-01-02 13:00")

    def test_an_invalid_stored_timezone_does_not_crash_the_request(self):
        """ProfileForm always validates against the real IANA list, but
        a hand-edited/stale value (direct ORM write, admin, a future
        tzdata removal) shouldn't 500 the whole app — falls back to
        settings.TIME_ZONE instead."""
        self.alice.timezone = "Not/A_Real_Zone"
        self.alice.save()
        response = self.client.get(reverse("workouts:session-list"))
        self.assertEqual(response.status_code, 200)

    def test_misleading_timezone_aliases_are_not_offered_as_choices(self):
        """"localtime" reads as "use my device's own timezone" but is
        actually a fixed server-side alias (whatever /etc/localtime
        resolves to in the container, typically UTC) — nothing about it
        is dynamic, so offering it just reproduces the exact confusion
        this whole feature fixes. "Factory" is tzdata's own placeholder
        for "no real zone", never a meaningful choice."""
        response = self.client.get(reverse("profile"))
        self.assertNotContains(response, 'value="localtime"')
        self.assertNotContains(response, 'value="Factory"')
        self.assertContains(response, 'value="Europe/Helsinki"')


class SignupFlowTests(TestCase):
    def test_signup_creates_user_and_logs_in(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "newlifter",
                "email": "newlifter@example.com",
                "password1": "a-very-strong-pass-1",
                "password2": "a-very-strong-pass-1",
            },
        )
        self.assertRedirects(response, reverse("dashboard"))
        self.assertTrue(User.objects.filter(username="newlifter").exists())
        self.assertIn("_auth_user_id", self.client.session)


class LoginFlowTests(TestCase):
    def test_login_then_access_dashboard(self):
        User.objects.create_user(username="bob", password="s3cret-pass")
        login_ok = self.client.login(username="bob", password="s3cret-pass")
        self.assertTrue(login_ok)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_cross_user_session_isolation(self):
        User.objects.create_user(username="carol", password="s3cret-pass")
        User.objects.create_user(username="dave", password="s3cret-pass")
        self.client.login(username="carol", password="s3cret-pass")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.user.username, "carol")


class ProfileViewTests(TestCase):
    """Phase 11 polish: the "Profile" nav link was a dead `href="#"`
    placeholder since Phase 1, even though unit_system/timezone have
    driven unit conversion since Phase 8 with no UI to ever change them."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 302)

    def test_profile_shows_the_current_user_only(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.context["object"], self.alice)
        self.assertNotEqual(response.context["object"], bob)

    def test_updating_unit_system_and_timezone(self):
        response = self.client.post(
            reverse("profile"),
            {"unit_system": "imperial", "timezone": "America/New_York", "language": "en"},
        )
        self.assertRedirects(response, reverse("profile"))
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.unit_system, "imperial")
        self.assertEqual(self.alice.timezone, "America/New_York")

    def test_invalid_timezone_is_rejected(self):
        response = self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "Not/A_Real_Zone", "language": "en"},
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with errors
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.timezone, "UTC")

    def test_setting_height_in_cm_stores_the_canonical_meters_value(self):
        from decimal import Decimal

        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "height": "180", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.height, Decimal("1.8000"))

    def test_setting_height_in_inches_stores_the_canonical_meters_value(self):
        from decimal import Decimal

        self.alice.unit_system = "imperial"
        self.alice.save()
        self.client.post(
            reverse("profile"),
            {"unit_system": "imperial", "timezone": "UTC", "height": "70", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.height, Decimal("1.7780"))

    def test_edit_form_prefills_height_in_the_users_preferred_unit(self):
        from decimal import Decimal

        self.alice.height = Decimal("1.8")
        self.alice.save()
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Height (cm)")
        self.assertContains(response, 'value="180.0"')

    def test_clearing_height_sets_it_to_none(self):
        from decimal import Decimal

        self.alice.height = Decimal("1.8")
        self.alice.save()
        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "height": "", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertIsNone(self.alice.height)

    def test_show_bmi_defaults_to_true(self):
        self.assertTrue(self.alice.show_bmi)

    def test_unchecking_show_bmi_turns_it_off(self):
        # An unchecked checkbox simply isn't sent in the POST body.
        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.show_bmi)

    def test_checking_show_bmi_turns_it_back_on(self):
        self.alice.show_bmi = False
        self.alice.save()
        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "show_bmi": "on", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.show_bmi)

    def test_bmi_category_ranges_are_shown_even_with_no_height_or_weight_yet(self):
        """Regression: the BMI category ranges only ever appeared inside
        the dashboard's BMI card, which itself only rendered once both a
        height and a logged body weight existed — so a user who hadn't
        gotten that far yet had no way to find the scale at all. The
        profile page (where height and the show_bmi toggle both live)
        shows it unconditionally instead."""
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Underweight")
        self.assertContains(response, "Normal weight")
        self.assertContains(response, "Overweight")
        self.assertContains(response, "Obese")

    def test_bmi_ranges_are_hidden_on_profile_when_show_bmi_is_off(self):
        self.alice.show_bmi = False
        self.alice.save()
        response = self.client.get(reverse("profile"))
        self.assertNotContains(response, "Normal weight")

    def test_current_bmi_is_shown_on_profile_once_computable(self):
        from decimal import Decimal

        from apps.measurements.models import BodyMeasurement, MeasurementType

        self.alice.height = Decimal("1.80")
        self.alice.save()
        body_weight_type = MeasurementType.objects.get(name="Body weight", owner=None)
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=body_weight_type, value=Decimal("82.5")
        )
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "25.5")
        self.assertContains(response, "Overweight")

    def test_show_bmi_renders_as_an_inline_checkbox_with_its_own_label(self):
        """Regression: every field (including a lone checkbox) rendered
        through the generic block-level label_tag + field layout, which
        stacked "Show BMI on the dashboard" above an isolated checkbox
        instead of the two sitting next to each other."""
        response = self.client.get(reverse("profile"))
        self.assertContains(response, 'class="checkbox-field"')
        self.assertContains(response, "Show")
        self.assertContains(response, "on the dashboard")
        self.assertContains(response, "Turns off the BMI card")

    def test_show_achievements_defaults_to_true(self):
        self.assertTrue(self.alice.show_achievements)

    def test_unchecking_show_achievements_turns_it_off(self):
        # An unchecked checkbox simply isn't sent in the POST body.
        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.show_achievements)

    def test_checking_show_achievements_turns_it_back_on(self):
        self.alice.show_achievements = False
        self.alice.save()
        self.client.post(
            reverse("profile"),
            {
                "unit_system": "metric",
                "timezone": "UTC",
                "show_achievements": "on",
                "language": "en",
            },
        )
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.show_achievements)

    def test_show_achievements_field_is_on_the_profile_page(self):
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Share my activity")
        self.assertContains(response, "keep your own activity private")


class PasswordChangeTests(TestCase):
    """The URLs (django.contrib.auth.urls) already existed since Phase 1,
    but with no templates — visiting them would 500. Phase 11 polish."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="old-pass-123")
        self.client.login(username="alice", password="old-pass-123")

    def test_password_change_form_renders(self):
        response = self.client.get(reverse("password_change"))
        self.assertEqual(response.status_code, 200)

    def test_changing_password_logs_future_requests_in_with_the_new_one(self):
        response = self.client.post(
            reverse("password_change"),
            {
                "old_password": "old-pass-123",
                "new_password1": "a-very-strong-new-pass-1",
                "new_password2": "a-very-strong-new-pass-1",
            },
        )
        self.assertRedirects(response, reverse("password_change_done"))
        self.client.logout()
        self.assertTrue(self.client.login(username="alice", password="a-very-strong-new-pass-1"))
