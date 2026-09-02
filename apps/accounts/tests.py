import hashlib
import io
import json
import re
import zipfile
from decimal import Decimal
from urllib.parse import urlparse

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts import twofactor
from apps.accounts.forms import LOGIN_ATTEMPT_LIMIT, PASSWORD_RESET_ATTEMPT_LIMIT
from apps.accounts.models import SiteDisclaimer
from apps.accounts.views import RateLimitedLoginView

User = get_user_model()


class CustomUserModelTests(TestCase):
    def test_user_has_unit_and_timezone_preferences(self):
        user = User.objects.create_user(username="alice", password="s3cret-pass")
        self.assertEqual(user.unit_system, "metric")
        self.assertEqual(user.timezone, "UTC")

    def test_language_defaults_to_english(self):
        user = User.objects.create_user(username="alice", password="s3cret-pass")
        self.assertEqual(user.language, "en")


class PublicDisplayNameTests(TestCase):
    """User.public_display_name() — what other users see for this user
    (apps.analytics.achievements); a separate concern from this user's
    own dashboard greeting (apps.core.greetings), which always uses
    their first name regardless of this setting."""

    def test_falls_back_to_the_username_with_no_first_name_set(self):
        user = User.objects.create_user(username="alice", password="s3cret-pass")
        self.assertEqual(user.public_display_name(), "alice")

    def test_includes_the_first_name_when_set_and_opted_in(self):
        user = User.objects.create_user(
            username="alice", password="s3cret-pass", first_name="Alice"
        )
        self.assertEqual(user.public_display_name(), "alice (Alice)")

    def test_falls_back_to_the_username_when_opted_out_even_with_a_first_name_set(self):
        user = User.objects.create_user(
            username="alice",
            password="s3cret-pass",
            first_name="Alice",
            show_name_to_others=False,
        )
        self.assertEqual(user.public_display_name(), "alice")

    def test_show_name_to_others_defaults_to_true(self):
        user = User.objects.create_user(username="alice", password="s3cret-pass")
        self.assertTrue(user.show_name_to_others)


class GravatarTests(TestCase):
    """User.gravatar_url()/show_gravatar — see docs/SECURITY.md
    "Gravatar profile picture" for why this defaults off unlike this
    app's other display/privacy toggles: it's the only place a user's
    browser talks to a server outside this instance."""

    def test_show_gravatar_defaults_to_false(self):
        user = User.objects.create_user(username="alice", password="s3cret-pass")
        self.assertFalse(user.show_gravatar)

    def test_gravatar_url_hashes_the_lowercased_trimmed_email(self):
        user = User.objects.create_user(
            username="alice", password="s3cret-pass", email=" Alice@Example.com "
        )
        expected_hash = hashlib.sha256(b"alice@example.com").hexdigest()
        self.assertIn(expected_hash, user.gravatar_url())

    def test_gravatar_url_asks_for_a_404_instead_of_a_placeholder(self):
        user = User.objects.create_user(
            username="alice", password="s3cret-pass", email="alice@example.com"
        )
        self.assertIn("d=404", user.gravatar_url())


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


class SignupGatingTests(TestCase):
    """docs/SECURITY.md — DJANGO_SIGNUP_ENABLED. Gates the URL itself,
    not just the login page's link to it: a hidden link doesn't stop
    someone who already knows/guesses the path."""

    def test_signup_page_is_reachable_by_default(self):
        response = self.client.get(reverse("signup"))
        self.assertEqual(response.status_code, 200)

    def test_signup_link_shown_on_login_page_by_default(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, reverse("signup"))

    @override_settings(SIGNUP_ENABLED=False)
    def test_signup_page_redirects_to_login_when_disabled(self):
        response = self.client.get(reverse("signup"), follow=True)
        self.assertRedirects(response, reverse("login"))
        self.assertContains(response, "Registration is currently closed.")

    @override_settings(SIGNUP_ENABLED=False)
    def test_signup_post_is_also_blocked_when_disabled(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "sneaky",
                "email": "sneaky@example.com",
                "password1": "a-very-strong-pass-1",
                "password2": "a-very-strong-pass-1",
            },
        )
        self.assertRedirects(response, reverse("login"))
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    @override_settings(SIGNUP_ENABLED=False)
    def test_signup_link_hidden_on_login_page_when_disabled(self):
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, reverse("signup"))

    @override_settings(SIGNUP_ENABLED=False)
    def test_existing_users_can_still_log_in_when_signup_is_disabled(self):
        User.objects.create_user(username="already-here", password="s3cret-pass")
        login_ok = self.client.login(username="already-here", password="s3cret-pass")
        self.assertTrue(login_ok)


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


class LoginRateLimitTests(TestCase):
    """apps.accounts.forms.RateLimitedAuthenticationForm — Django's own
    login view has no brute-force protection at all otherwise (this is
    a completely separate mechanism from apps.api's rate limiting,
    which only ever applies to API keys)."""

    def setUp(self):
        User.objects.create_user(username="alice", password="s3cret-pass")
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _attempt(self, password="wrong-password", ip="203.0.113.10"):
        return self.client.post(
            reverse("login"),
            {"username": "alice", "password": password},
            REMOTE_ADDR=ip,
            HTTP_X_REAL_IP=ip,
        )

    def test_failed_attempts_under_the_limit_are_not_blocked(self):
        for _ in range(LOGIN_ATTEMPT_LIMIT - 1):
            response = self._attempt()
            self.assertNotContains(response, "Too many failed login attempts")

    def test_the_nth_failed_attempt_locks_out_further_tries(self):
        for _ in range(LOGIN_ATTEMPT_LIMIT):
            self._attempt()
        response = self._attempt()
        self.assertContains(response, "Too many failed login attempts")

    def test_lockout_blocks_even_the_correct_password(self):
        for _ in range(LOGIN_ATTEMPT_LIMIT):
            self._attempt()
        response = self._attempt(password="s3cret-pass")
        self.assertContains(response, "Too many failed login attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lockout_is_keyed_per_ip_not_globally(self):
        for _ in range(LOGIN_ATTEMPT_LIMIT):
            self._attempt(ip="203.0.113.10")
        response = self._attempt(ip="203.0.113.99")
        self.assertNotContains(response, "Too many failed login attempts")

    def test_a_successful_login_resets_the_counter(self):
        for _ in range(LOGIN_ATTEMPT_LIMIT - 1):
            self._attempt()
        response = self._attempt(password="s3cret-pass")
        self.assertIn("_auth_user_id", self.client.session)
        self.client.logout()
        # Back under the limit again — the earlier near-lockout was
        # cleared by the successful login, not just paused.
        for _ in range(LOGIN_ATTEMPT_LIMIT - 1):
            response = self._attempt()
            self.assertNotContains(response, "Too many failed login attempts")


class AdminLoginRateLimitTests(TestCase):
    """apps.accounts.forms.RateLimitedAdminAuthenticationForm —
    /admin/login/ is Django's own, completely separate login view/form
    from the one LoginRateLimitTests above covers; without this it
    stayed wide open to brute-force even after the regular login got
    rate-limited."""

    def setUp(self):
        User.objects.create_user(
            username="admin", password="s3cret-pass", is_staff=True, is_superuser=True
        )
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _attempt(self, password="wrong-password", ip="203.0.113.10"):
        return self.client.post(
            reverse("admin:login"),
            {"username": "admin", "password": password},
            REMOTE_ADDR=ip,
            HTTP_X_REAL_IP=ip,
        )

    def test_the_nth_failed_attempt_locks_out_further_tries(self):
        for _ in range(LOGIN_ATTEMPT_LIMIT):
            self._attempt()
        response = self._attempt()
        self.assertContains(response, "Too many failed login attempts")

    def test_lockout_blocks_even_the_correct_password(self):
        for _ in range(LOGIN_ATTEMPT_LIMIT):
            self._attempt()
        response = self._attempt(password="s3cret-pass")
        self.assertContains(response, "Too many failed login attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admin_lockout_is_a_separate_counter_from_the_regular_login(self):
        """Same client IP, same near-simultaneous failed attempts on
        both endpoints — locking out /admin/login/ must not also lock
        out /accounts/login/ for that IP, and vice versa."""
        for _ in range(LOGIN_ATTEMPT_LIMIT):
            self._attempt(ip="203.0.113.10")
        response = self.client.post(
            reverse("login"),
            {"username": "admin", "password": "wrong-password"},
            REMOTE_ADDR="203.0.113.10",
            HTTP_X_REAL_IP="203.0.113.10",
        )
        self.assertNotContains(response, "Too many failed login attempts")


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

    def test_changelog_modal_is_closed_by_default(self):
        response = self.client.get(reverse("profile"))
        self.assertFalse(response.context["open_changelog"])
        self.assertContains(response, "x-data=\"{ open: false }\"")

    def test_changelog_query_param_opens_the_modal(self):
        # apps.core.management.commands.announce_version_update's push
        # notification links here — a Web Push notification can only
        # ever open a URL, so this is how it lands the user straight
        # in the changelog rather than just the plain profile page.
        response = self.client.get(reverse("profile"), {"changelog": "1"})
        self.assertTrue(response.context["open_changelog"])
        self.assertContains(response, "x-data=\"{ open: true }\"")

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

    def test_show_bmi_renders_as_an_inline_checkbox_with_its_own_label(self):
        """Regression: every field (including a lone checkbox) rendered
        through the generic block-level label_tag + field layout, which
        stacked "Show BMI on the dashboard" above an isolated checkbox
        instead of the two sitting next to each other."""
        response = self.client.get(reverse("profile"))
        self.assertContains(response, 'class="checkbox-field"')
        self.assertContains(response, "Show")
        self.assertContains(response, "on the body weight page")
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

    def test_show_name_to_others_defaults_to_true(self):
        self.assertTrue(self.alice.show_name_to_others)

    def test_unchecking_show_name_to_others_turns_it_off(self):
        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.show_name_to_others)

    def test_checking_show_name_to_others_turns_it_back_on(self):
        self.alice.show_name_to_others = False
        self.alice.save()
        self.client.post(
            reverse("profile"),
            {
                "unit_system": "metric",
                "timezone": "UTC",
                "show_name_to_others": "on",
                "language": "en",
            },
        )
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.show_name_to_others)

    def test_show_name_to_others_field_is_on_the_profile_page(self):
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Show my name to others")

    def test_show_gravatar_field_is_on_the_profile_page(self):
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Show my Gravatar picture")

    def test_gravatar_image_is_not_rendered_by_default(self):
        response = self.client.get(reverse("profile"))
        self.assertNotContains(response, "gravatar.com/avatar")

    def test_checking_show_gravatar_turns_it_on_and_renders_the_image(self):
        self.client.post(
            reverse("profile"),
            {
                "unit_system": "metric",
                "timezone": "UTC",
                "show_gravatar": "on",
                "language": "en",
            },
        )
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.show_gravatar)
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "gravatar.com/avatar")

    def test_unchecking_show_gravatar_turns_it_back_off(self):
        self.alice.show_gravatar = True
        self.alice.save()
        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.show_gravatar)

    def test_allow_friend_requests_and_allow_group_invites_default_to_true(self):
        self.assertTrue(self.alice.allow_friend_requests)
        self.assertTrue(self.alice.allow_group_invites)

    def test_unchecking_allow_friend_requests_turns_it_off(self):
        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.allow_friend_requests)

    def test_unchecking_allow_group_invites_turns_it_off(self):
        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "language": "en"},
        )
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.allow_group_invites)

    def test_checking_allow_friend_requests_and_allow_group_invites_keeps_them_on(self):
        self.client.post(
            reverse("profile"),
            {
                "unit_system": "metric",
                "timezone": "UTC",
                "allow_friend_requests": "on",
                "allow_group_invites": "on",
                "language": "en",
            },
        )
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.allow_friend_requests)
        self.assertTrue(self.alice.allow_group_invites)

    def test_admin_link_is_hidden_for_a_regular_user(self):
        response = self.client.get(reverse("profile"))
        self.assertNotContains(response, reverse("admin:index"))

    def test_admin_link_is_shown_for_staff(self):
        self.alice.is_staff = True
        self.alice.save()
        response = self.client.get(reverse("profile"))
        self.assertContains(response, reverse("admin:index"))

    def test_saving_preferences_shows_a_dismissable_toast_not_a_static_card(self):
        """Regression: "Preferences saved." used to render as a plain,
        permanent .card at the top of <main>, staying on screen until
        the next full page navigation happened to push it off — now the
        same top-of-screen toast every other Django message (and PR
        notice) uses."""
        response = self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "language": "en"},
            follow=True,
        )
        self.assertContains(response, "Preferences saved.")
        self.assertContains(response, 'id="pr-toast-container"')
        self.assertContains(response, "pr-banner")

    def test_account_details_password_and_api_key_cards_each_have_their_own_cta_button(self):
        """Regression: the whole "Change password"/"API keys" card used
        to be one big <a>, with nothing visually marking it as
        clickable. Each card is now a plain (non-link) container with
        an explicit .button-secondary as the only link."""
        response = self.client.get(reverse("profile"))
        # Account details, Change password, Two-factor authentication,
        # Friends & groups, API keys, Download your data, Feedback,
        # Delete account.
        self.assertContains(response, 'class="card card-action-row"', count=8)
        self.assertContains(
            response, f'<a class="button-secondary" href="{reverse("account-details")}">'
        )
        self.assertContains(
            response, f'<a class="button-secondary" href="{reverse("password_change")}">'
        )
        self.assertContains(
            response,
            f'<a class="button-secondary" href="{reverse("api_keys:key-list")}">',
        )

    def test_admin_card_also_has_its_own_cta_button_when_shown(self):
        self.alice.is_staff = True
        self.alice.save()
        response = self.client.get(reverse("profile"))
        # Account details, Change password, Two-factor authentication,
        # Friends & groups, API keys, Download your data, Feedback,
        # Delete account + Admin, Backups, Feedback, Site & SEO (the
        # latter four inside the staff-only "danger zone").
        self.assertContains(response, 'class="card card-action-row"', count=12)
        self.assertContains(
            response, f'<a class="button-secondary" href="{reverse("admin:index")}">'
        )
        self.assertContains(response, 'class="danger-zone"')
        self.assertContains(response, reverse("backup-list"))


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


class PasswordResetFlowTests(TestCase):
    """django.contrib.auth.urls already wired these URLs up, but with
    no templates (they'd 500) and no EMAIL_BACKEND configured — the
    only self-service recovery for a forgotten password was an admin
    manually resetting it via /admin/. See docs/SECURITY.md "Email"."""

    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", password="old-pass-123", email="alice@example.com"
        )

    def test_reset_form_renders(self):
        response = self.client.get(reverse("password_reset"))
        self.assertEqual(response.status_code, 200)

    def test_submitting_a_known_email_sends_a_reset_email(self):
        response = self.client.post(reverse("password_reset"), {"email": "alice@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("alice@example.com", mail.outbox[0].to)
        self.assertIn("alice", mail.outbox[0].body)  # the username reminder

    def test_submitting_an_unknown_email_shows_the_same_confirmation(self):
        """No account-enumeration tell: the response looks identical
        whether or not the address actually belongs to an account."""
        response = self.client.post(
            reverse("password_reset"), {"email": "nobody@example.com"}, follow=True
        )
        self.assertContains(response, "If an account exists with that email address")
        self.assertEqual(len(mail.outbox), 0)

    def test_following_the_emailed_link_resets_the_password(self):
        self.client.post(reverse("password_reset"), {"email": "alice@example.com"})
        match = re.search(r"https?://[^\s]+/accounts/reset/[^\s]+", mail.outbox[0].body)
        self.assertIsNotNone(match, mail.outbox[0].body)
        reset_path = urlparse(match.group(0)).path

        # GET redirects the one-time token in the URL to a session-
        # backed "set-password" URL (Django's own anti-Referer-leak
        # mechanism) — that's the page/URL the form actually posts to.
        confirm_response = self.client.get(reset_path, follow=True)
        self.assertContains(confirm_response, "Set new password")
        set_password_url = confirm_response.wsgi_request.path

        response = self.client.post(
            set_password_url,
            {
                "new_password1": "a-brand-new-strong-pass-1",
                "new_password2": "a-brand-new-strong-pass-1",
            },
            follow=True,
        )
        self.assertContains(response, "Your password has been set")
        self.assertTrue(
            self.client.login(username="alice", password="a-brand-new-strong-pass-1")
        )

    def test_an_invalid_token_shows_the_invalid_link_message(self):
        uidb64 = urlsafe_base64_encode(force_bytes(self.alice.pk))
        response = self.client.get(
            reverse("password_reset_confirm", kwargs={"uidb64": uidb64, "token": "bogus-token"}),
            follow=True,
        )
        self.assertContains(response, "invalid, possibly because it has already been used")


class PasswordResetRateLimitTests(TestCase):
    """apps.accounts.forms.RateLimitedPasswordResetForm — without it,
    PasswordResetView is wide open to spamming an arbitrary email
    address with reset links using this instance's own SMTP relay."""

    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", password="old-pass-123", email="alice@example.com"
        )
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _request(self, email="alice@example.com", ip="203.0.113.10"):
        return self.client.post(
            reverse("password_reset"),
            {"email": email},
            REMOTE_ADDR=ip,
            HTTP_X_REAL_IP=ip,
        )

    def test_requests_under_the_limit_are_not_blocked(self):
        for _ in range(PASSWORD_RESET_ATTEMPT_LIMIT - 1):
            response = self._request()
            self.assertRedirects(response, reverse("password_reset_done"))

    def test_the_nth_request_locks_out_further_tries(self):
        for _ in range(PASSWORD_RESET_ATTEMPT_LIMIT):
            self._request()
        response = self._request()
        self.assertContains(response, "Too many password reset requests")
        # The earlier, allowed requests sent real emails; the blocked
        # one over the limit must not send a further one.
        self.assertEqual(len(mail.outbox), PASSWORD_RESET_ATTEMPT_LIMIT)

    def test_every_request_counts_toward_the_limit_not_just_ones_that_send_an_email(self):
        """Unlike a login attempt, there's no "failed" password-reset
        submission to count selectively — a request for an unknown
        email is just as valid a submission (and just as easy to spam
        with) as one for a real address, even though it sends no
        email."""
        for _ in range(PASSWORD_RESET_ATTEMPT_LIMIT):
            self._request(email="nobody@example.com")
        response = self._request(email="nobody@example.com")
        self.assertContains(response, "Too many password reset requests")

    def test_lockout_is_keyed_per_ip_not_globally(self):
        for _ in range(PASSWORD_RESET_ATTEMPT_LIMIT):
            self._request(ip="203.0.113.10")
        response = self._request(ip="203.0.113.99")
        self.assertRedirects(response, reverse("password_reset_done"))


class AccountDetailsTests(TestCase):
    """Username/name/email — a separate page from ProfileView's display
    preferences and from the password itself, linked from the profile
    page's "Account details" card."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("account-details"))
        self.assertEqual(response.status_code, 302)

    def test_form_renders_prefilled_with_the_current_users_details(self):
        self.alice.first_name = "Alice"
        self.alice.email = "alice@example.com"
        self.alice.save()
        response = self.client.get(reverse("account-details"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice")
        self.assertContains(response, "alice@example.com")

    def test_updating_details_saves_and_shows_a_toast(self):
        response = self.client.post(
            reverse("account-details"),
            {
                "username": "alice",
                "first_name": "Alice",
                "last_name": "Smith",
                "email": "alice.smith@example.com",
            },
            follow=True,
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.first_name, "Alice")
        self.assertEqual(self.alice.last_name, "Smith")
        self.assertEqual(self.alice.email, "alice.smith@example.com")
        self.assertContains(response, "Account details saved.")
        self.assertContains(response, 'id="pr-toast-container"')

    def test_username_can_be_changed_and_still_used_to_log_in(self):
        response = self.client.post(
            reverse("account-details"),
            {"username": "alice2", "first_name": "", "last_name": "", "email": ""},
        )
        self.assertRedirects(response, reverse("account-details"))
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.username, "alice2")
        self.client.logout()
        self.assertTrue(self.client.login(username="alice2", password="s3cret-pass"))

    def test_username_must_stay_unique(self):
        User.objects.create_user(username="bob", password="s3cret-pass")
        response = self.client.post(
            reverse("account-details"),
            {"username": "bob", "first_name": "", "last_name": "", "email": ""},
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with errors
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.username, "alice")

    def test_account_details_card_links_next_to_change_password_on_profile(self):
        response = self.client.get(reverse("profile"))
        content = response.content.decode()
        account_pos = content.find(reverse("account-details"))
        password_pos = content.find(reverse("password_change"))
        self.assertNotEqual(account_pos, -1)
        self.assertNotEqual(password_pos, -1)
        # "Next to" — no other card-action-row between the two.
        between = content[account_pos:password_pos]
        self.assertEqual(between.count("card-action-row"), 1)


class TwoFactorServiceTests(TestCase):
    """apps.accounts.twofactor — the RFC 6238 math itself is pyotp's
    job; this covers the thin IronStack-specific layer on top."""

    def setUp(self):
        self.user = User.objects.create_user(username="frank", password="s3cret-pass")

    def test_generate_totp_secret_returns_a_valid_base32_string(self):
        import pyotp

        secret = twofactor.generate_totp_secret()
        # pyotp.TOTP() raising nothing and producing a 6-digit code is
        # proof enough that this is a well-formed base32 secret.
        code = pyotp.TOTP(secret).now()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_verify_totp_code_accepts_the_current_real_code(self):
        import pyotp

        secret = twofactor.generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        self.assertTrue(twofactor.verify_totp_code(secret, code))

    def test_verify_totp_code_rejects_a_wrong_code(self):
        secret = twofactor.generate_totp_secret()
        self.assertFalse(twofactor.verify_totp_code(secret, "000000"))

    def test_provisioning_uri_names_the_issuer_and_the_username(self):
        secret = twofactor.generate_totp_secret()
        uri = twofactor.provisioning_uri(self.user, secret)
        self.assertIn("IronStack", uri)
        self.assertIn("frank", uri)

    def test_qr_code_data_uri_is_a_real_png(self):
        uri = twofactor.provisioning_uri(self.user, twofactor.generate_totp_secret())
        data_uri = twofactor.qr_code_data_uri(uri)
        self.assertTrue(data_uri.startswith("data:image/png;base64,"))

    def test_generate_backup_codes_returns_the_configured_count(self):
        codes = twofactor.generate_backup_codes(self.user)
        self.assertEqual(len(codes), twofactor.BACKUP_CODE_COUNT)
        self.assertEqual(self.user.backup_codes.count(), twofactor.BACKUP_CODE_COUNT)

    def test_generate_backup_codes_replaces_any_existing_set(self):
        first_batch = twofactor.generate_backup_codes(self.user)
        second_batch = twofactor.generate_backup_codes(self.user)
        self.assertEqual(self.user.backup_codes.count(), twofactor.BACKUP_CODE_COUNT)
        self.assertNotEqual(set(first_batch), set(second_batch))

    def test_verify_and_consume_backup_code_accepts_once_then_rejects(self):
        codes = twofactor.generate_backup_codes(self.user)
        self.assertTrue(twofactor.verify_and_consume_backup_code(self.user, codes[0]))
        self.assertFalse(twofactor.verify_and_consume_backup_code(self.user, codes[0]))

    def test_verify_and_consume_backup_code_rejects_an_unknown_code(self):
        twofactor.generate_backup_codes(self.user)
        self.assertFalse(
            twofactor.verify_and_consume_backup_code(self.user, "0000-0000-0000-0000")
        )


class TwoFactorSetupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="grace", password="s3cret-pass")
        self.client.login(username="grace", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("two-factor-setup"))
        self.assertEqual(response.status_code, 302)

    def test_get_generates_and_saves_a_secret_immediately(self):
        self.assertEqual(self.user.totp_secret, "")
        response = self.client.get(reverse("two-factor-setup"))
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.totp_secret, "")
        self.assertFalse(self.user.totp_enabled)  # not yet confirmed

    def test_get_shows_a_qr_code_and_the_secret_as_text(self):
        response = self.client.get(reverse("two-factor-setup"))
        self.assertContains(response, "data:image/png;base64,")
        self.user.refresh_from_db()
        self.assertContains(response, self.user.totp_secret)

    def test_already_enabled_redirects_to_profile(self):
        self.user.totp_enabled = True
        self.user.totp_secret = twofactor.generate_totp_secret()
        self.user.save()
        response = self.client.get(reverse("two-factor-setup"))
        self.assertRedirects(response, reverse("profile"))

    def test_confirming_with_the_correct_code_enables_2fa_and_redirects_to_backup_codes(self):
        """Backup codes aren't generated in this same request any more
        — see TwoFactorBackupCodesView's own docstring for why
        (they're slow enough to need their own loading page instead of
        this request silently hanging for them)."""
        import pyotp

        self.client.get(reverse("two-factor-setup"))  # generates the secret
        self.user.refresh_from_db()
        code = pyotp.TOTP(self.user.totp_secret).now()
        response = self.client.post(reverse("two-factor-setup"), {"code": code})
        self.assertRedirects(response, f"{reverse('two-factor-backup-codes')}?welcome=1")
        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)
        self.assertEqual(self.user.backup_codes.count(), 0)

    def test_confirming_with_the_wrong_code_does_not_enable_2fa(self):
        self.client.get(reverse("two-factor-setup"))
        response = self.client.post(reverse("two-factor-setup"), {"code": "000000"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "field-error")
        self.user.refresh_from_db()
        self.assertFalse(self.user.totp_enabled)


class TwoFactorLoginFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="henry", password="s3cret-pass")
        self.user.totp_secret = twofactor.generate_totp_secret()
        self.user.totp_enabled = True
        self.user.save()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _current_code(self):
        import pyotp

        return pyotp.TOTP(self.user.totp_secret).now()

    def test_correct_password_redirects_to_verify_instead_of_logging_in(self):
        response = self.client.post(
            reverse("login"), {"username": "henry", "password": "s3cret-pass"}
        )
        self.assertRedirects(
            response, reverse("two-factor-verify") + "?next=/", fetch_redirect_response=False
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_verify_without_a_pending_login_redirects_to_login(self):
        response = self.client.get(reverse("two-factor-verify"))
        self.assertRedirects(response, reverse("login"))

    def test_correct_totp_code_completes_login(self):
        self.client.post(reverse("login"), {"username": "henry", "password": "s3cret-pass"})
        response = self.client.post(reverse("two-factor-verify"), {"code": self._current_code()})
        self.assertRedirects(response, "/")
        self.assertIn("_auth_user_id", self.client.session)

    def test_wrong_code_does_not_complete_login(self):
        self.client.post(reverse("login"), {"username": "henry", "password": "s3cret-pass"})
        response = self.client.post(reverse("two-factor-verify"), {"code": "000000"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Incorrect code")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_valid_backup_code_completes_login_and_is_consumed(self):
        codes = twofactor.generate_backup_codes(self.user)
        self.client.post(reverse("login"), {"username": "henry", "password": "s3cret-pass"})
        response = self.client.post(reverse("two-factor-verify"), {"code": codes[0]})
        self.assertRedirects(response, "/")
        self.assertEqual(self.user.backup_codes.filter(used_at__isnull=False).count(), 1)

    def test_a_user_without_2fa_logs_in_directly(self):
        User.objects.create_user(username="iris", password="s3cret-pass")
        response = self.client.post(
            reverse("login"), {"username": "iris", "password": "s3cret-pass"}
        )
        self.assertRedirects(response, "/")
        self.assertIn("_auth_user_id", self.client.session)


class TwoFactorVerifyRateLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="jack", password="s3cret-pass")
        self.user.totp_secret = twofactor.generate_totp_secret()
        self.user.totp_enabled = True
        self.user.save()
        cache.clear()
        self.client.post(reverse("login"), {"username": "jack", "password": "s3cret-pass"})

    def tearDown(self):
        cache.clear()

    def _attempt(self):
        return self.client.post(reverse("two-factor-verify"), {"code": "000000"})

    def test_the_nth_wrong_attempt_locks_out_further_tries(self):
        from apps.accounts.forms import TWOFACTOR_ATTEMPT_LIMIT

        for _ in range(TWOFACTOR_ATTEMPT_LIMIT):
            self._attempt()
        response = self._attempt()
        self.assertContains(response, "Too many incorrect codes")

    def test_lockout_blocks_even_the_correct_code(self):
        import pyotp

        from apps.accounts.forms import TWOFACTOR_ATTEMPT_LIMIT

        for _ in range(TWOFACTOR_ATTEMPT_LIMIT):
            self._attempt()
        code = pyotp.TOTP(self.user.totp_secret).now()
        response = self.client.post(reverse("two-factor-verify"), {"code": code})
        self.assertContains(response, "Too many incorrect codes")
        self.assertNotIn("_auth_user_id", self.client.session)


class TwoFactorDisableTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="karen", password="s3cret-pass")
        self.user.totp_secret = twofactor.generate_totp_secret()
        self.user.totp_enabled = True
        self.user.save()
        twofactor.generate_backup_codes(self.user)
        self.client.login(username="karen", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("two-factor-disable"))
        self.assertEqual(response.status_code, 302)

    def test_redirects_to_profile_if_2fa_isnt_enabled(self):
        self.user.totp_enabled = False
        self.user.save()
        response = self.client.get(reverse("two-factor-disable"))
        self.assertRedirects(response, reverse("profile"))

    def test_wrong_password_does_not_disable(self):
        response = self.client.post(reverse("two-factor-disable"), {"password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Incorrect password")
        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)

    def test_correct_password_disables_and_clears_everything(self):
        response = self.client.post(reverse("two-factor-disable"), {"password": "s3cret-pass"})
        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.totp_enabled)
        self.assertEqual(self.user.totp_secret, "")
        self.assertEqual(self.user.backup_codes.count(), 0)


class TwoFactorRegenerateBackupCodesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="liam", password="s3cret-pass")
        self.user.totp_secret = twofactor.generate_totp_secret()
        self.user.totp_enabled = True
        self.user.save()
        self.old_codes = twofactor.generate_backup_codes(self.user)
        self.client.login(username="liam", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.post(reverse("two-factor-regenerate-backup-codes"))
        self.assertEqual(response.status_code, 302)

    def test_requires_2fa_enabled(self):
        self.user.totp_enabled = False
        self.user.save()
        response = self.client.post(reverse("two-factor-regenerate-backup-codes"))
        self.assertEqual(response.status_code, 404)

    def test_redirects_to_the_backup_codes_page_without_generating_anything_itself(self):
        """The actual generation moved to TwoFactorBackupCodesFragment
        View, loaded via HTMX from the page this redirects to — see
        that view's own docstring for why."""
        response = self.client.post(reverse("two-factor-regenerate-backup-codes"))
        self.assertRedirects(response, reverse("two-factor-backup-codes"))
        self.assertEqual(self.user.backup_codes.count(), twofactor.BACKUP_CODE_COUNT)
        for old_code in self.old_codes:
            self.assertTrue(twofactor.verify_and_consume_backup_code(self.user, old_code))


class TwoFactorBackupCodesViewTests(TestCase):
    """The loading-state page both TwoFactorSetupView's confirm step
    and TwoFactorRegenerateBackupCodesView redirect to — see
    TwoFactorBackupCodesView's own docstring for why generating a
    fresh set of codes needed a page of its own instead of happening
    silently inside whichever request landed the user here."""

    def setUp(self):
        self.user = User.objects.create_user(username="liam", password="s3cret-pass")
        self.user.totp_secret = twofactor.generate_totp_secret()
        self.user.totp_enabled = True
        self.user.save()
        self.client.login(username="liam", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("two-factor-backup-codes"))
        self.assertEqual(response.status_code, 302)

    def test_requires_2fa_enabled(self):
        self.user.totp_enabled = False
        self.user.save()
        response = self.client.get(reverse("two-factor-backup-codes"))
        self.assertEqual(response.status_code, 404)

    def test_renders_a_loading_state_without_generating_anything_yet(self):
        response = self.client.get(reverse("two-factor-backup-codes"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "spinner")
        self.assertEqual(self.user.backup_codes.count(), 0)

    def test_shows_the_welcome_message_only_when_asked_to(self):
        response = self.client.get(reverse("two-factor-backup-codes"), {"welcome": "1"})
        self.assertContains(response, "now enabled")

        response = self.client.get(reverse("two-factor-backup-codes"))
        self.assertNotContains(response, "now enabled")


class TwoFactorBackupCodesFragmentViewTests(TestCase):
    """The HTMX-loaded fragment that actually does the (slow) backup-
    code generation — see TwoFactorBackupCodesView's own docstring."""

    def setUp(self):
        self.user = User.objects.create_user(username="liam", password="s3cret-pass")
        self.user.totp_secret = twofactor.generate_totp_secret()
        self.user.totp_enabled = True
        self.user.save()
        self.client.login(username="liam", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.post(reverse("two-factor-backup-codes-fragment"))
        self.assertEqual(response.status_code, 302)

    def test_requires_2fa_enabled(self):
        self.user.totp_enabled = False
        self.user.save()
        response = self.client.post(reverse("two-factor-backup-codes-fragment"))
        self.assertEqual(response.status_code, 404)

    def test_generates_and_shows_a_fresh_set_of_codes(self):
        response = self.client.post(reverse("two-factor-backup-codes-fragment"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Done")
        self.assertEqual(self.user.backup_codes.count(), twofactor.BACKUP_CODE_COUNT)


class TwoFactorAdminActionTests(TestCase):
    def test_disable_two_factor_action_clears_everything(self):
        from apps.accounts.admin import UserAdmin

        user = User.objects.create_user(username="mona", password="s3cret-pass")
        user.totp_secret = twofactor.generate_totp_secret()
        user.totp_enabled = True
        user.save()
        twofactor.generate_backup_codes(user)

        UserAdmin(User, admin.site).disable_two_factor(None, User.objects.filter(pk=user.pk))

        user.refresh_from_db()
        self.assertFalse(user.totp_enabled)
        self.assertEqual(user.totp_secret, "")
        self.assertEqual(user.backup_codes.count(), 0)


class DeleteAccountServiceTests(TestCase):
    """apps.accounts.services.delete_account — GDPR Article 17
    self-service erasure. See that function's own docstring for the
    two different treatments (hard-delete vs. reassign-to-shared) and
    why."""

    def setUp(self):
        from apps.accounts.services import delete_account

        self.delete_account = delete_account
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_deletes_the_user_row_itself(self):
        self.delete_account(self.alice)
        self.assertFalse(User.objects.filter(pk=self.alice.pk).exists())

    def test_hard_deletes_exclusively_personal_data(self):
        from django.utils import timezone

        from apps.exercises.models import Exercise
        from apps.records.models import PersonalRecord, PRType
        from apps.workouts import services as workout_services

        session = workout_services.start_session(self.alice, workout=None)
        workout_services.complete_session(session)
        exercise = Exercise.objects.create(name="Test Snatch", owner=None)
        PersonalRecord.objects.create(
            user=self.alice,
            exercise=exercise,
            record_type=PRType.MAX_WEIGHT,
            value=Decimal("100"),
            weight=Decimal("100"),
            reps=1,
            achieved_at=timezone.now(),
        )
        self.delete_account(self.alice)
        self.assertEqual(PersonalRecord.objects.count(), 0)

    def test_reassigns_a_shared_custom_exercise_instead_of_deleting_it(self):
        from apps.exercises.models import Exercise

        exercise = Exercise.objects.create(name="Alice's Curl", owner=self.alice)
        self.delete_account(self.alice)
        exercise.refresh_from_db()
        self.assertIsNone(exercise.owner)

    def test_does_not_orphan_or_break_another_users_workout_history(self):
        """The real reason for the reassign-not-delete behavior:
        Exercise's own usage FK (apps.workouts.models.
        PerformedExercise.exercise) is on_delete=PROTECT specifically
        so a still-referenced Exercise can never vanish out from under
        someone still using it."""
        from apps.exercises.models import Exercise
        from apps.workouts import services as workout_services

        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        exercise = Exercise.objects.create(name="Shared Curl", owner=self.alice)
        session = workout_services.start_session(bob, workout=None)
        performed = workout_services.add_performed_exercise(session, exercise)
        workout_services.log_set(performed, weight=Decimal("20"), reps=10)
        workout_services.complete_session(session)

        self.delete_account(self.alice)

        performed.refresh_from_db()
        self.assertEqual(performed.exercise_id, exercise.pk)
        self.assertTrue(bob.workout_sessions.exists())

    def test_reassigns_shared_content_across_every_ownable_app(self):
        from apps.activities.models import ActivityType
        from apps.measurements.models import MeasurementType
        from apps.nutrition.models import Food, MealSlot, Recipe
        from apps.programs.models import Program

        rows = {
            ActivityType: ActivityType.objects.create(name="Alice's Hobby", owner=self.alice),
            MeasurementType: MeasurementType.objects.create(
                name="Alice's Metric", unit_kind="length", owner=self.alice
            ),
            Food: Food.objects.create(
                name="Alice's Food", owner=self.alice, calories=100,
                protein_grams=Decimal("1"), carbohydrate_grams=Decimal("1"),
                fat_grams=Decimal("1"), serving_size=Decimal("100"), serving_unit="g",
            ),
            MealSlot: MealSlot.objects.create(name="Alice's Meal", owner=self.alice),
            Recipe: Recipe.objects.create(name="Alice's Recipe", owner=self.alice),
            Program: Program.objects.create(name="Alice's Program", owner=self.alice),
        }
        self.delete_account(self.alice)
        for model, row in rows.items():
            row.refresh_from_db()
            self.assertIsNone(row.owner, f"{model.__name__} still owned after delete_account")


class AccountDeleteViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("account-delete"))
        self.assertEqual(response.status_code, 302)

    def test_wrong_password_does_not_delete(self):
        response = self.client.post(reverse("account-delete"), {"password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Incorrect password")
        self.assertTrue(User.objects.filter(pk=self.alice.pk).exists())

    def test_correct_password_deletes_and_logs_out(self):
        response = self.client.post(
            reverse("account-delete"), {"password": "s3cret-pass"}, follow=True
        )
        self.assertRedirects(response, reverse("login"))
        self.assertFalse(User.objects.filter(username="alice").exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_an_sso_user_confirms_with_their_username_instead_of_a_password(self):
        self.alice.is_sso_user = True
        self.alice.set_unusable_password()
        self.alice.save()
        # Client.login() itself needs a real password to authenticate
        # against — force_login bypasses that the same way an actual
        # Authentik-issued session would (apps.accounts.oidc), not a
        # username/password form submission at all.
        self.client.force_login(self.alice)

        wrong = self.client.post(reverse("account-delete"), {"confirm_username": "not-alice"})
        self.assertEqual(wrong.status_code, 200)
        self.assertContains(wrong, "Doesn&#x27;t match your username.")
        self.assertTrue(User.objects.filter(username="alice").exists())

        right = self.client.post(reverse("account-delete"), {"confirm_username": "alice"})
        self.assertRedirects(right, reverse("login"))
        self.assertFalse(User.objects.filter(username="alice").exists())

    def test_the_last_superuser_cannot_delete_themselves(self):
        self.alice.is_superuser = True
        self.alice.is_staff = True
        self.alice.save()
        response = self.client.post(
            reverse("account-delete"), {"password": "s3cret-pass"}, follow=True
        )
        self.assertRedirects(response, reverse("profile"))
        self.assertTrue(User.objects.filter(username="alice").exists())

    def test_a_superuser_can_delete_themselves_if_another_superuser_remains(self):
        self.alice.is_superuser = True
        self.alice.is_staff = True
        self.alice.save()
        User.objects.create_superuser(username="bob", password="s3cret-pass")
        response = self.client.post(
            reverse("account-delete"), {"password": "s3cret-pass"}, follow=True
        )
        self.assertRedirects(response, reverse("login"))
        self.assertFalse(User.objects.filter(username="alice").exists())

    def test_the_profile_page_links_to_it(self):
        response = self.client.get(reverse("profile"))
        self.assertContains(response, reverse("account-delete"))


class DataExportServiceTests(TestCase):
    """apps.accounts.services.export_account_data — GDPR Article 20
    ("right to data portability"). Mirrors delete_account's own set of
    "exclusively personal" models, plus this user's own authored
    shared content (a custom exercise they created, say), since it's
    a read of what exists today, not a statement about what account
    deletion would do to each of it."""

    def setUp(self):
        from apps.accounts.services import export_account_data

        self.export_account_data = export_account_data
        self.alice = User.objects.create_user(
            username="alice",
            password="s3cret-pass",
            email="alice@example.com",
            first_name="Alice",
            last_name="Anderson",
        )

    def test_includes_basic_account_fields(self):
        data = self.export_account_data(self.alice)
        self.assertEqual(data["account"]["username"], "alice")
        self.assertEqual(data["account"]["email"], "alice@example.com")
        self.assertEqual(data["account"]["first_name"], "Alice")
        self.assertEqual(data["account"]["last_name"], "Anderson")

    def test_includes_display_and_privacy_settings_too(self):
        # Not just the handful of fields shown on the profile form —
        # everything on the user record that isn't a credential (see
        # export_account_data's own comment on why `password`/
        # `totp_secret` are the only two exceptions).
        self.alice.height = Decimal("1.8000")
        self.alice.show_bmi = False
        self.alice.show_gravatar = True
        self.alice.save()
        data = self.export_account_data(self.alice)
        self.assertEqual(data["account"]["height_meters"], "1.8000")
        self.assertIs(data["account"]["show_bmi"], False)
        self.assertIs(data["account"]["show_gravatar"], True)

    def test_never_includes_the_password_or_totp_secret(self):
        data = self.export_account_data(self.alice)
        self.assertNotIn("password", data["account"])
        self.assertNotIn("totp_secret", data["account"])

    def test_includes_friends_and_pending_friend_requests(self):
        from apps.social import services as social_services

        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        carol = User.objects.create_user(username="carol", password="s3cret-pass")
        social_services.send_friend_request(self.alice, bob)
        social_services.send_friend_request(carol, self.alice)

        data = self.export_account_data(self.alice)
        self.assertEqual(data["friend_requests_sent"][0]["to"], "bob")
        self.assertEqual(data["friend_requests_sent"][0]["status"], "pending")
        self.assertEqual(data["friend_requests_received"][0]["from"], "carol")

    def test_includes_friendships_group_memberships_and_messages(self):
        from apps.social import services as social_services
        from apps.social.models import FriendRequest as SocialFriendRequest

        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        social_services.send_friend_request(self.alice, bob)
        request = SocialFriendRequest.objects.get(from_user=self.alice, to_user=bob)
        social_services.accept_friend_request(request, acting_user=bob)
        social_services.send_direct_message(self.alice, bob, "hello")
        social_services.send_direct_message(bob, self.alice, "hi back")

        group = social_services.create_group(self.alice, "Lifters")
        social_services.send_group_message(group, self.alice, "welcome")

        data = self.export_account_data(self.alice)
        self.assertEqual(data["friends"], [{"username": "bob"}])
        self.assertEqual(len(data["direct_messages"]), 2)
        self.assertEqual(data["group_memberships"][0]["group"], "Lifters")
        self.assertEqual(data["group_messages_sent"][0]["body"], "welcome")

    def test_includes_blocked_users(self):
        from apps.social import services as social_services

        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        social_services.block_user(self.alice, bob)
        data = self.export_account_data(self.alice)
        self.assertEqual(data["blocked_users"][0]["username"], "bob")

    def test_includes_workout_history(self):
        from apps.exercises.models import Exercise
        from apps.workouts import services as workout_services

        exercise = Exercise.objects.create(name="Test Curl", owner=None)
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, exercise)
        workout_services.log_set(performed, weight=Decimal("20"), reps=10)
        workout_services.complete_session(session)

        data = self.export_account_data(self.alice)
        self.assertEqual(len(data["workout_sessions"]), 1)
        self.assertEqual(len(data["performed_exercises"]), 1)
        self.assertEqual(len(data["exercise_sets"]), 1)
        self.assertEqual(data["exercise_sets"][0]["fields"]["weight"], "20.00")

    def test_includes_a_custom_exercise_this_user_created(self):
        from apps.exercises.models import Exercise

        Exercise.objects.create(name="Alice's Curl", owner=self.alice)
        data = self.export_account_data(self.alice)
        self.assertEqual(len(data["custom_exercises"]), 1)

    def test_never_includes_another_users_data(self):
        from apps.exercises.models import Exercise
        from apps.workouts import services as workout_services

        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        exercise = Exercise.objects.create(name="Test Curl", owner=None)
        session = workout_services.start_session(bob, workout=None)
        workout_services.add_performed_exercise(session, exercise)

        data = self.export_account_data(self.alice)
        self.assertEqual(data["workout_sessions"], [])
        self.assertEqual(data["performed_exercises"], [])

    def test_api_key_export_never_includes_the_key_hash(self):
        from apps.api.models import ApiKey, RateLimitTier

        tier = RateLimitTier.objects.create(name="Default")
        ApiKey.objects.create(
            user=self.alice, name="My key", key_hash="a" * 64, prefix="abcd1234", tier=tier
        )
        data = self.export_account_data(self.alice)
        self.assertEqual(len(data["api_keys"]), 1)
        self.assertNotIn("key_hash", data["api_keys"][0])
        self.assertEqual(data["api_keys"][0]["prefix"], "abcd1234")

    def test_result_is_actually_json_serializable(self):
        import json

        from apps.exercises.models import Exercise
        from apps.workouts import services as workout_services

        exercise = Exercise.objects.create(name="Test Curl", owner=None)
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, exercise)
        workout_services.log_set(performed, weight=Decimal("20"), reps=10)
        workout_services.complete_session(session)

        data = self.export_account_data(self.alice)
        json.dumps(data)  # raises if anything isn't JSON-safe


class DataExportViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("data-export"))
        self.assertEqual(response.status_code, 302)

    def test_default_response_is_a_browsable_html_page(self):
        response = self.client.get(reverse("data-export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"].split(";")[0], "text/html")

    def test_a_hand_built_section_does_not_500(self):
        # Regression test: export_account_data hand-builds several
        # sections (push_subscriptions and every apps.social section)
        # as flat lists of dicts rather than through Django's generic
        # serializer, the same way `api_keys` already is. _rows_for_section
        # used to only know about `account`/`api_keys` as hand-built and
        # tried to read every other section as {"pk", "fields"} dicts —
        # a real user with so much as one push subscription or friend
        # hit a KeyError on "pk" and got a 500 on this page.
        from apps.core.models import PushSubscription

        PushSubscription.objects.create(
            user=self.alice,
            endpoint="https://push.example.com/abc123",
            p256dh_key="a-key",
            auth_key="an-auth-key",
        )
        response = self.client.get(reverse("data-export"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "push_subscriptions")

    def test_json_format_is_a_downloadable_attachment(self):
        response = self.client.get(reverse("data-export"), {"format": "json"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("attachment", response["Content-Disposition"])
        json.loads(response.content)  # a real, parseable JSON body

    def test_csv_format_is_a_downloadable_zip(self):
        response = self.client.get(reverse("data-export"), {"format": "csv"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("attachment", response["Content-Disposition"])
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertIn("account.csv", archive.namelist())

    def test_the_profile_page_links_to_it(self):
        response = self.client.get(reverse("profile"))
        self.assertContains(response, reverse("data-export"))

    def test_html_format_is_a_downloadable_attachment(self):
        response = self.client.get(reverse("data-export"), {"format": "html"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"].split(";")[0], "text/html")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(f"ironstack-{self.alice.username}-data.html", response["Content-Disposition"])

    def test_html_download_contains_the_same_sections_as_the_browsable_page(self):
        response = self.client.get(reverse("data-export"), {"format": "html"})
        self.assertContains(response, "account")

    def test_sections_use_native_details_not_javascript_so_a_downloaded_copy_still_works(self):
        # The whole point of "Download as HTML" is a file that still
        # shows its data once saved and reopened somewhere this app's
        # own JS never loads again — see static/css/base.css's own
        # comment on `.card > summary` for why this is `<details>`,
        # not Alpine's x-show/x-cloak.
        response = self.client.get(reverse("data-export"), {"format": "html"})
        self.assertContains(response, "<details")
        self.assertNotContains(response, "x-cloak")

    def test_html_download_is_fully_self_contained(self):
        # A real regression, not a hypothetical one: an earlier version
        # of this download reused the browsable page's own template
        # (extends base.html), whose nav-bar <svg class="nav-icon">
        # elements are sized entirely by base.css — invisible here,
        # since a downloaded file has no guarantee that stylesheet is
        # ever reachable again, so every icon rendered at its raw,
        # enormous intrinsic SVG size instead. The download now uses
        # its own dedicated template with every style inline and no
        # nav, icons, or scripts at all.
        response = self.client.get(reverse("data-export"), {"format": "html"})
        content = response.content.decode()
        self.assertIn("<style>", content)
        self.assertNotIn("stylesheet", content)
        self.assertNotIn("<script", content)
        self.assertNotIn("nav-icon", content)
        self.assertNotIn('class="bottom-nav"', content)


class SiteDisclaimerTests(TestCase):
    def test_load_creates_the_singleton_with_the_default_text(self):
        disclaimer = SiteDisclaimer.load()
        self.assertIn("not responsible for any data loss", disclaimer.text)

    def test_load_always_returns_the_same_row(self):
        first = SiteDisclaimer.load()
        first.text = "Custom text"
        first.save()
        second = SiteDisclaimer.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.text, "Custom text")

    def test_delete_is_a_no_op(self):
        disclaimer = SiteDisclaimer.load()
        disclaimer.delete()
        self.assertTrue(SiteDisclaimer.objects.filter(pk=1).exists())

    def test_shown_on_the_login_page(self):
        disclaimer = SiteDisclaimer.load()
        disclaimer.text = "A distinctive test disclaimer string."
        disclaimer.save()
        response = self.client.get(reverse("login"))
        self.assertContains(response, "A distinctive test disclaimer string.")

    def test_shown_on_the_signup_page(self):
        disclaimer = SiteDisclaimer.load()
        disclaimer.text = "A distinctive test disclaimer string."
        disclaimer.save()
        response = self.client.get(reverse("signup"))
        self.assertContains(response, "A distinctive test disclaimer string.")

    def test_blank_text_hides_the_footer_entirely(self):
        disclaimer = SiteDisclaimer.load()
        disclaimer.text = ""
        disclaimer.save()
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, "auth-disclaimer")


class PrivacyNoticeTests(TestCase):
    """templates/accounts/_privacy_notice_modal.html — a fixed,
    translated notice (unlike SiteDisclaimer above, which is free-form
    operator-authored text), shown at the point data collection starts
    (login/signup) and reachable again later from the profile page."""

    def test_shown_on_the_login_page(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Privacy notice")

    def test_shown_on_the_signup_page(self):
        response = self.client.get(reverse("signup"))
        self.assertContains(response, "Privacy notice")

    def test_reachable_again_from_the_profile_page(self):
        User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Privacy notice")


class AuthPageBrandingTests(TestCase):
    def test_login_page_shows_the_ironstack_brand_header(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "auth-brand")
        self.assertContains(response, "IronStack")

    def test_signup_page_shows_the_ironstack_brand_header(self):
        response = self.client.get(reverse("signup"))
        self.assertContains(response, "auth-brand")
        self.assertContains(response, "IronStack")


class OnboardingModelTests(TestCase):
    def test_new_users_default_to_not_onboarded(self):
        user = User.objects.create_user(username="nora", password="s3cret-pass")
        self.assertFalse(user.onboarding_completed)


class OnboardingContextProcessorTests(TestCase):
    """apps.accounts.context_processors.onboarding — merged into every
    page's context (templates/base.html always includes
    accounts/_onboarding_modal.html), not just one view's."""

    def test_anonymous_visitor_never_sees_it(self):
        response = self.client.get(reverse("login"))
        self.assertNotIn("show_onboarding", response.context)

    def test_a_not_yet_onboarded_user_sees_it_on_an_unrelated_page(self):
        User.objects.create_user(username="oscar", password="s3cret-pass")
        self.client.login(username="oscar", password="s3cret-pass")
        response = self.client.get(reverse("profile"))
        self.assertTrue(response.context["show_onboarding"])
        self.assertContains(response, "Welcome to IronStack")

    def test_an_already_onboarded_user_never_sees_it(self):
        user = User.objects.create_user(username="paula", password="s3cret-pass")
        user.onboarding_completed = True
        user.save()
        self.client.login(username="paula", password="s3cret-pass")
        response = self.client.get(reverse("profile"))
        self.assertNotIn("show_onboarding", response.context)
        self.assertNotContains(response, "Welcome to IronStack")


class OnboardingViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="quinn", password="s3cret-pass")
        self.client.login(username="quinn", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.post(reverse("onboarding"), {"action": "skip"})
        self.assertEqual(response.status_code, 302)

    def test_saving_updates_the_user_and_marks_onboarding_complete(self):
        response = self.client.post(
            reverse("onboarding"),
            {
                "action": "save",
                "first_name": "Quinn",
                "email": "quinn@example.com",
                "unit_system": "metric",
                "timezone": "UTC",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding_completed)
        self.assertEqual(self.user.first_name, "Quinn")
        self.assertEqual(self.user.email, "quinn@example.com")

    def test_every_field_is_optional_an_entirely_blank_save_still_completes_it(self):
        response = self.client.post(
            reverse("onboarding"), {"action": "save", "unit_system": "metric", "timezone": "UTC"}
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding_completed)

    def test_a_weight_is_logged_as_a_body_weight_measurement_not_a_user_field(self):
        from apps.measurements.models import BodyMeasurement

        self.client.post(
            reverse("onboarding"),
            {"action": "save", "unit_system": "metric", "timezone": "UTC", "weight": "80.5"},
        )
        measurement = BodyMeasurement.objects.get(
            user=self.user, measurement_type__name="Body weight"
        )
        self.assertEqual(measurement.value, Decimal("80.5000"))

    def test_a_weight_entered_in_pounds_is_converted_to_canonical_kilograms(self):
        from apps.measurements.models import BodyMeasurement

        self.client.post(
            reverse("onboarding"),
            {"action": "save", "unit_system": "imperial", "timezone": "UTC", "weight": "220"},
        )
        measurement = BodyMeasurement.objects.get(
            user=self.user, measurement_type__name="Body weight"
        )
        self.assertAlmostEqual(float(measurement.value), 99.79, places=1)

    def test_a_height_in_cm_is_converted_to_canonical_meters_on_the_user(self):
        self.client.post(
            reverse("onboarding"),
            {"action": "save", "unit_system": "metric", "timezone": "UTC", "height": "180.5"},
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.height, Decimal("1.8050"))

    def test_a_height_in_inches_is_converted_to_canonical_meters_on_the_user(self):
        self.client.post(
            reverse("onboarding"),
            {"action": "save", "unit_system": "imperial", "timezone": "UTC", "height": "70"},
        )
        self.user.refresh_from_db()
        self.assertAlmostEqual(float(self.user.height), 1.778, places=3)

    def test_leaving_height_blank_leaves_it_unset(self):
        self.client.post(
            reverse("onboarding"),
            {"action": "save", "unit_system": "metric", "timezone": "UTC"},
        )
        self.user.refresh_from_db()
        self.assertIsNone(self.user.height)

    def test_leaving_weight_blank_creates_no_measurement(self):
        from apps.measurements.models import BodyMeasurement

        self.client.post(
            reverse("onboarding"),
            {"action": "save", "unit_system": "metric", "timezone": "UTC"},
        )
        self.assertFalse(BodyMeasurement.objects.filter(user=self.user).exists())

    def test_an_invalid_email_re_renders_the_modal_with_the_error_and_does_not_complete_it(self):
        response = self.client.post(
            reverse("onboarding"),
            {"action": "save", "email": "not-an-email", "unit_system": "metric", "timezone": "UTC"},
        )
        self.assertContains(response, "field-error")
        self.user.refresh_from_db()
        self.assertFalse(self.user.onboarding_completed)

    def test_skip_marks_onboarding_complete_without_saving_any_field(self):
        response = self.client.post(
            reverse("onboarding"), {"action": "skip", "first_name": "Ignored"}
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding_completed)
        self.assertEqual(self.user.first_name, "")

    def test_skip_after_a_failed_save_does_not_persist_the_invalid_attempt(self):
        self.client.post(
            reverse("onboarding"),
            {"action": "save", "email": "not-an-email", "unit_system": "metric", "timezone": "UTC"},
        )
        self.client.post(reverse("onboarding"), {"action": "skip"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding_completed)
        self.assertEqual(self.user.email, "")

    def test_success_response_no_longer_contains_the_modal(self):
        response = self.client.post(
            reverse("onboarding"), {"action": "save", "unit_system": "metric", "timezone": "UTC"}
        )
        self.assertNotContains(response, "Welcome to IronStack")
        self.assertContains(response, 'id="onboarding-modal-container"')

    def test_saving_persists_the_chosen_timezone(self):
        self.client.post(
            reverse("onboarding"),
            {"action": "save", "unit_system": "metric", "timezone": "Europe/Helsinki"},
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "Europe/Helsinki")

    def test_timezone_is_required_a_missing_value_re_renders_with_an_error(self):
        response = self.client.post(
            reverse("onboarding"), {"action": "save", "unit_system": "metric"}
        )
        self.assertContains(response, "field-error")
        self.user.refresh_from_db()
        self.assertFalse(self.user.onboarding_completed)

    def test_the_form_is_pre_filled_with_the_users_current_timezone(self):
        self.user.timezone = "Europe/Helsinki"
        self.user.save(update_fields=["timezone"])
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Europe/Helsinki")

    def test_allow_friend_requests_and_allow_group_invites_default_to_checked(self):
        # Both settings default to True on the user itself; the
        # onboarding checkboxes should start checked to match, not
        # force a fresh account to opt back in to its own default.
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'id="id_allow_friend_requests" checked')
        self.assertContains(response, 'id="id_allow_group_invites" checked')

    def test_unchecking_allow_friend_requests_and_allow_group_invites_turns_them_off(self):
        self.client.post(
            reverse("onboarding"),
            {"action": "save", "unit_system": "metric", "timezone": "UTC"},
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.allow_friend_requests)
        self.assertFalse(self.user.allow_group_invites)

    def test_leaving_them_checked_keeps_the_default_on(self):
        self.client.post(
            reverse("onboarding"),
            {
                "action": "save",
                "unit_system": "metric",
                "timezone": "UTC",
                "allow_friend_requests": "on",
                "allow_group_invites": "on",
            },
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.allow_friend_requests)
        self.assertTrue(self.user.allow_group_invites)


class PasswordLoginGatingTests(TestCase):
    """docs/SECURITY.md "Single sign-on (Authentik / OIDC)" —
    DJANGO_PASSWORD_LOGIN_ENABLED. Same "gate the URL/POST itself, not
    just hide the link" approach as SignupGatingTests above for
    DJANGO_SIGNUP_ENABLED, applied to local password login/signup/
    password-reset once Authentik is meant to be the only way in."""

    def test_login_form_shown_by_default(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, 'name="password"')

    @override_settings(PASSWORD_LOGIN_ENABLED=False)
    def test_login_form_hidden_when_password_login_disabled(self):
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, 'name="password"')

    @override_settings(PASSWORD_LOGIN_ENABLED=False)
    def test_login_post_is_blocked_when_password_login_disabled(self):
        User.objects.create_user(username="pat", password="s3cret-pass")
        response = self.client.post(
            reverse("login"), {"username": "pat", "password": "s3cret-pass"}
        )
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(PASSWORD_LOGIN_ENABLED=False, SIGNUP_ENABLED=True)
    def test_signup_is_blocked_when_password_login_disabled_even_if_signup_enabled(self):
        response = self.client.get(reverse("signup"), follow=True)
        self.assertRedirects(response, reverse("login"))

    @override_settings(PASSWORD_LOGIN_ENABLED=False)
    def test_password_reset_is_blocked_when_password_login_disabled(self):
        response = self.client.get(reverse("password_reset"), follow=True)
        self.assertRedirects(response, reverse("login"))

    @override_settings(PASSWORD_LOGIN_ENABLED=False)
    def test_forgot_password_link_hidden_when_password_login_disabled(self):
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, reverse("password_reset"))

    def test_admin_login_is_unaffected_by_password_login_disabled(self):
        # apps.core.admin's own separate login view — a break-glass
        # path that must survive a misconfigured/unreachable Authentik
        # instance (docs/SECURITY.md).
        with override_settings(PASSWORD_LOGIN_ENABLED=False):
            response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="password"')


class AuthentikLoginButtonTests(TestCase):
    """settings.AUTHENTIK_ENABLED is computed once, at process startup,
    from whether AUTHENTIK_URL/AUTHENTIK_CLIENT_ID/AUTHENTIK_CLIENT_SECRET
    are set — like config.urls' conditional inclusion of
    mozilla_django_oidc's own urls, override_settings can flip the
    template-visible flag for a test but can't retroactively register
    urlpatterns that were only ever built once at import time. So this
    only covers the template-visible half (the button itself); the
    "oidc/authenticate/" URL only actually exists on a process that
    was started with real AUTHENTIK_* values set."""

    @override_settings(AUTHENTIK_ENABLED=False)
    def test_button_hidden_by_default(self):
        # Explicit override rather than relying on the ambient
        # settings.AUTHENTIK_ENABLED: a dev environment whose own .env
        # already has real AUTHENTIK_* values set (e.g. to manually
        # verify the SSO flow against a real Authentik instance) would
        # otherwise make this test falsely fail there, even though
        # nothing about the app itself is broken — see this class's own
        # docstring for why the override here doesn't hit the
        # "urlpatterns fixed at process startup" problem that stops the
        # *enabled* case from being tested the same way.
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, "Log in with Authentik")

    @override_settings(AUTHENTIK_ENABLED=True)
    def test_login_view_context_flag_is_set_when_authentik_enabled(self):
        # Not a full self.client.get(reverse("login")) render: the
        # template's {% url 'oidc_authentication_init' %} would raise
        # NoReverseMatch here, since override_settings can flip this
        # flag but — unlike a real deployment started with real
        # AUTHENTIK_* values — can't retroactively register the
        # mozilla_django_oidc urls config.urls only ever included once,
        # at process-startup import time. So this checks the view's own
        # context data directly instead of the rendered page.
        request = RequestFactory().get(reverse("login"))
        request.user = AnonymousUser()
        view = RateLimitedLoginView()
        view.setup(request)
        self.assertTrue(view.get_context_data()["authentik_enabled"])


class OIDCAuthenticationBackendTests(TestCase):
    """apps.accounts.oidc.IronStackOIDCAuthenticationBackend — unit
    tests against its own claim-handling methods directly, not the
    full mozilla_django_oidc.auth.OIDCAuthenticationBackend.authenticate()
    flow (that would mean mocking the token/userinfo HTTP round-trip
    to Authentik, which is mozilla-django-oidc's own already-tested
    responsibility, not this project's)."""

    def setUp(self):
        from apps.accounts.oidc import IronStackOIDCAuthenticationBackend

        self.backend = IronStackOIDCAuthenticationBackend.__new__(
            IronStackOIDCAuthenticationBackend
        )
        self.backend.UserModel = User

    def test_existing_local_account_is_matched_by_email(self):
        User.objects.create_user(
            username="quinn", password="s3cret-pass", email="quinn@example.com"
        )
        matched = self.backend.filter_users_by_claims({"email": "quinn@example.com"})
        self.assertEqual(list(matched), [User.objects.get(username="quinn")])

    def test_get_username_prefers_authentik_preferred_username(self):
        username = self.backend.get_username(
            {"email": "riley@example.com", "preferred_username": "riley"}
        )
        self.assertEqual(username, "riley")

    def test_get_username_deduplicates_on_collision_with_an_existing_account(self):
        User.objects.create_user(username="sam", password="s3cret-pass")
        username = self.backend.get_username(
            {"email": "sam2@example.com", "preferred_username": "sam"}
        )
        self.assertEqual(username, "sam2")

    def test_create_user_sets_unusable_password_and_is_sso_user(self):
        user = self.backend.create_user(
            {"email": "tara@example.com", "preferred_username": "tara", "name": "Tara Tester"}
        )
        self.assertTrue(user.is_sso_user)
        self.assertFalse(user.has_usable_password())

    def test_create_user_takes_only_the_first_word_of_authentiks_full_name_claim(self):
        # Authentik has no first/last name split of its own — its
        # default OpenID 'profile' scope mapping sends the account's
        # single full-name field as "name" (docs/SECURITY.md "Single
        # sign-on (Authentik / OIDC)"). Only the first word should ever
        # land on first_name, not the whole string.
        user = self.backend.create_user(
            {"email": "tara@example.com", "preferred_username": "tara", "name": "Tara Tester"}
        )
        self.assertEqual(user.first_name, "Tara")

    def test_update_user_also_takes_only_the_first_word_of_the_name_claim(self):
        user = User.objects.create_user(
            username="willa", password="s3cret-pass", email="willa@example.com"
        )
        self.backend.update_user(user, {"email": "willa@example.com", "name": "Willa Wonka"})
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Willa")

    @override_settings(AUTHENTIK_REQUIRED_GROUP="")
    def test_verify_claims_passes_by_default_with_no_required_group(self):
        # Explicit override rather than relying on the ambient default
        # (settings.AUTHENTIK_REQUIRED_GROUP default is "" — see
        # config.settings.base) for the same reason as
        # AuthentikLoginButtonTests.test_button_hidden_by_default's own
        # override: a dev environment's .env may already have a real
        # AUTHENTIK_REQUIRED_GROUP set, which would otherwise leak into
        # this "no required group" case and fail it.
        self.assertTrue(
            self.backend.verify_claims({"email": "vic@example.com", "groups": []})
        )

    @override_settings(AUTHENTIK_REQUIRED_GROUP="ironstack-users")
    def test_verify_claims_rejects_a_user_missing_the_required_group(self):
        self.assertFalse(
            self.backend.verify_claims(
                {"email": "vic@example.com", "groups": ["some-other-app-users"]}
            )
        )

    @override_settings(AUTHENTIK_REQUIRED_GROUP="ironstack-users")
    def test_verify_claims_passes_a_user_in_the_required_group(self):
        self.assertTrue(
            self.backend.verify_claims(
                {"email": "vic@example.com", "groups": ["ironstack-users", "other"]}
            )
        )

    def test_update_user_marks_an_existing_local_account_as_sso_linked(self):
        user = User.objects.create_user(
            username="uma", password="s3cret-pass", email="uma@example.com"
        )
        self.backend.update_user(user, {"email": "uma@example.com", "name": "Uma"})
        user.refresh_from_db()
        self.assertTrue(user.is_sso_user)
        self.assertEqual(user.first_name, "Uma")
        # A local password set before ever linking Authentik stays
        # usable — this account can still log in with it unless/until
        # DJANGO_PASSWORD_LOGIN_ENABLED is turned off separately.
        self.assertTrue(user.has_usable_password())
