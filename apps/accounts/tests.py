import re
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.forms import LOGIN_ATTEMPT_LIMIT

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
        # Account details, Change password, API keys, Feedback.
        self.assertContains(response, 'class="card card-action-row"', count=4)
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
        # Account details, Change password, API keys, Feedback + Admin,
        # Backups, Feedback (the latter three inside the staff-only
        # "danger zone").
        self.assertContains(response, 'class="card card-action-row"', count=7)
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
