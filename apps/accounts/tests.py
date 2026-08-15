import re
from decimal import Decimal
from urllib.parse import urlparse

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts import twofactor
from apps.accounts.forms import LOGIN_ATTEMPT_LIMIT, PASSWORD_RESET_ATTEMPT_LIMIT
from apps.accounts.models import SiteDisclaimer

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
        # API keys, Feedback.
        self.assertContains(response, 'class="card card-action-row"', count=5)
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
        # API keys, Feedback + Admin, Backups, Feedback (the latter
        # three inside the staff-only "danger zone").
        self.assertContains(response, 'class="card card-action-row"', count=8)
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

    def test_confirming_with_the_correct_code_enables_2fa_and_shows_backup_codes(self):
        import pyotp

        self.client.get(reverse("two-factor-setup"))  # generates the secret
        self.user.refresh_from_db()
        code = pyotp.TOTP(self.user.totp_secret).now()
        response = self.client.post(reverse("two-factor-setup"), {"code": code})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "now enabled")
        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)
        self.assertEqual(self.user.backup_codes.count(), twofactor.BACKUP_CODE_COUNT)

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

    def test_replaces_the_codes_and_shows_the_new_set(self):
        response = self.client.post(reverse("two-factor-regenerate-backup-codes"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New backup codes")
        self.assertEqual(self.user.backup_codes.count(), twofactor.BACKUP_CODE_COUNT)
        for old_code in self.old_codes:
            self.assertFalse(twofactor.verify_and_consume_backup_code(self.user, old_code))


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
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding_completed)
        self.assertEqual(self.user.first_name, "Quinn")
        self.assertEqual(self.user.email, "quinn@example.com")

    def test_every_field_is_optional_an_entirely_blank_save_still_completes_it(self):
        response = self.client.post(
            reverse("onboarding"), {"action": "save", "unit_system": "metric"}
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding_completed)

    def test_a_weight_is_logged_as_a_body_weight_measurement_not_a_user_field(self):
        from apps.measurements.models import BodyMeasurement

        self.client.post(
            reverse("onboarding"),
            {"action": "save", "unit_system": "metric", "weight": "80.5"},
        )
        measurement = BodyMeasurement.objects.get(
            user=self.user, measurement_type__name="Body weight"
        )
        self.assertEqual(measurement.value, Decimal("80.5000"))

    def test_a_weight_entered_in_pounds_is_converted_to_canonical_kilograms(self):
        from apps.measurements.models import BodyMeasurement

        self.client.post(
            reverse("onboarding"),
            {"action": "save", "unit_system": "imperial", "weight": "220"},
        )
        measurement = BodyMeasurement.objects.get(
            user=self.user, measurement_type__name="Body weight"
        )
        self.assertAlmostEqual(float(measurement.value), 99.79, places=1)

    def test_leaving_weight_blank_creates_no_measurement(self):
        from apps.measurements.models import BodyMeasurement

        self.client.post(reverse("onboarding"), {"action": "save", "unit_system": "metric"})
        self.assertFalse(BodyMeasurement.objects.filter(user=self.user).exists())

    def test_an_invalid_email_re_renders_the_modal_with_the_error_and_does_not_complete_it(self):
        response = self.client.post(
            reverse("onboarding"),
            {"action": "save", "email": "not-an-email", "unit_system": "metric"},
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
            {"action": "save", "email": "not-an-email", "unit_system": "metric"},
        )
        self.client.post(reverse("onboarding"), {"action": "skip"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding_completed)
        self.assertEqual(self.user.email, "")

    def test_success_response_no_longer_contains_the_modal(self):
        response = self.client.post(
            reverse("onboarding"), {"action": "save", "unit_system": "metric"}
        )
        self.assertNotContains(response, "Welcome to IronStack")
        self.assertContains(response, 'id="onboarding-modal-container"')
