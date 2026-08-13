from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class CustomUserModelTests(TestCase):
    def test_user_has_unit_and_timezone_preferences(self):
        user = User.objects.create_user(username="alice", password="s3cret-pass")
        self.assertEqual(user.unit_system, "metric")
        self.assertEqual(user.timezone, "UTC")


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
            reverse("profile"), {"unit_system": "imperial", "timezone": "America/New_York"}
        )
        self.assertRedirects(response, reverse("profile"))
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.unit_system, "imperial")
        self.assertEqual(self.alice.timezone, "America/New_York")

    def test_invalid_timezone_is_rejected(self):
        response = self.client.post(
            reverse("profile"), {"unit_system": "metric", "timezone": "Not/A_Real_Zone"}
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with errors
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.timezone, "UTC")

    def test_setting_height_in_cm_stores_the_canonical_meters_value(self):
        from decimal import Decimal

        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "height": "180"},
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.height, Decimal("1.8000"))

    def test_setting_height_in_inches_stores_the_canonical_meters_value(self):
        from decimal import Decimal

        self.alice.unit_system = "imperial"
        self.alice.save()
        self.client.post(
            reverse("profile"),
            {"unit_system": "imperial", "timezone": "UTC", "height": "70"},
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
            reverse("profile"), {"unit_system": "metric", "timezone": "UTC", "height": ""}
        )
        self.alice.refresh_from_db()
        self.assertIsNone(self.alice.height)

    def test_show_bmi_defaults_to_true(self):
        self.assertTrue(self.alice.show_bmi)

    def test_unchecking_show_bmi_turns_it_off(self):
        # An unchecked checkbox simply isn't sent in the POST body.
        self.client.post(reverse("profile"), {"unit_system": "metric", "timezone": "UTC"})
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.show_bmi)

    def test_checking_show_bmi_turns_it_back_on(self):
        self.alice.show_bmi = False
        self.alice.save()
        self.client.post(
            reverse("profile"),
            {"unit_system": "metric", "timezone": "UTC", "show_bmi": "on"},
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
        self.assertContains(response, "Show BMI on the dashboard")
        self.assertContains(response, "Turns off the BMI card")


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
