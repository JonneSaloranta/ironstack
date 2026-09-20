"""Real-browser checks for the interactions the UI/UX audit added or fixed,
plus axe across more pages and every theme. Same gating as
test_accessibility.py (the `accessibility` marker, requirements/a11y.txt):
these need a real Chromium because what they assert only exists once real
JS, CSS and focus semantics run — the confirm dialog, modal focus trap,
HTMX error toast, and computed colour contrast in each theme."""

import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

import pytest

pytest.importorskip("playwright", reason="requirements/a11y.txt not installed")

from decimal import Decimal  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.urls import reverse  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from apps.accounts.models import Appearance, Theme  # noqa: E402
from apps.core.test_accessibility import _assert_no_serious_violations  # noqa: E402
from apps.exercises.models import Exercise  # noqa: E402
from apps.programs.models import Program, Workout  # noqa: E402
from apps.social import services as social_services  # noqa: E402
from apps.workouts import services as workout_services  # noqa: E402
from apps.workouts.models import WorkoutSession  # noqa: E402

User = get_user_model()

CALENDAR_OUTSIDE = [".calendar-day-outside"]  # see test_accessibility's own note


@pytest.mark.accessibility
class UiBehaviourTests(StaticLiveServerTestCase):
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
        self.page = self.browser.new_page()
        self.page.set_default_timeout(8000)
        self.addCleanup(self.page.close)
        self.alice = User.objects.create_user(
            username="alice", password="s3cret-pass", onboarding_completed=True
        )
        self.row = Exercise.objects.create(name="Row", owner=None)

    # -- helpers ---------------------------------------------------------
    def _url(self, path):
        return f"{self.live_server_url}{path}"

    def _log_in(self):
        self.page.goto(self._url("/accounts/login/"))
        self.page.fill("input[name=username]", "alice")
        self.page.fill("input[name=password]", "s3cret-pass")
        self.page.click("button[type=submit]")
        self.page.wait_for_load_state("networkidle")

    def _session_with_set(self):
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.row)
        workout_services.log_set(performed, weight=Decimal("60"), reps=5)
        return session, performed

    # -- confirm dialog ----------------------------------------------------
    def test_delete_asks_in_a_dialog_and_escape_cancels(self):
        session, _ = self._session_with_set()
        self._log_in()
        self.page.goto(self._url(reverse("workouts:session-detail", args=[session.pk])))
        self.page.get_by_role("button", name="Delete workout").click()
        dialog = self.page.locator("dialog.confirm-dialog")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("cannot be undone")
        self.page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        self.assertTrue(WorkoutSession.objects.filter(pk=session.pk).exists())

    def test_confirming_the_dialog_deletes(self):
        session, _ = self._session_with_set()
        self._log_in()
        self.page.goto(self._url(reverse("workouts:session-detail", args=[session.pk])))
        self.page.get_by_role("button", name="Delete workout").click()
        self.page.locator("dialog.confirm-dialog button[value=ok]").click()
        self.page.wait_for_url("**/workouts/")
        self.assertFalse(WorkoutSession.objects.filter(pk=session.pk).exists())

    def test_the_group_delete_prompt_with_an_apostrophe_renders_intact(self):
        group = social_services.create_group(self.alice, name="Lifters")
        self._log_in()
        self.page.goto(self._url(reverse("social:group-detail", args=[group.pk])))
        self.page.get_by_role("button", name="Delete group").click()
        expect(self.page.locator("dialog.confirm-dialog")).to_contain_text("can't be undone")

    def test_set_delete_confirms_through_the_same_dialog(self):
        session, _ = self._session_with_set()
        self._log_in()
        self.page.goto(self._url(reverse("workouts:session-detail", args=[session.pk])))
        self.page.locator(".set-actions button", has_text="Delete").first.click()
        expect(self.page.locator("dialog.confirm-dialog")).to_contain_text("Delete this set?")
        self.page.keyboard.press("Escape")

    # -- modal focus -------------------------------------------------------
    def test_modal_traps_focus_and_returns_it_on_close(self):
        self._log_in()
        self.page.goto(self._url("/accounts/profile/"))
        self.page.get_by_role("button", name="IronStack version").click()
        card = self.page.locator(".modal-card[aria-label=Changelog]")
        expect(card).to_be_visible()
        for _ in range(8):
            self.page.keyboard.press("Tab")
            inside = self.page.evaluate(
                "!!document.activeElement.closest('.modal-card[aria-label=Changelog]')"
            )
            self.assertTrue(inside, "Tab escaped the modal")
        self.page.keyboard.press("Escape")
        expect(card).to_be_hidden()
        self.assertIn(
            "IronStack version",
            self.page.evaluate("document.activeElement.textContent"),
        )

    # -- HTMX feedback -------------------------------------------------------
    def test_a_bad_set_is_explained_in_an_alert(self):
        session, _ = self._session_with_set()
        self._log_in()
        self.page.goto(self._url(reverse("workouts:session-train", args=[session.pk])))
        self.page.fill("#train-panel input[name=weight]", "")
        # The browser's own `required` check would stop an empty weight before it
        # ever reaches the server; this is about what the *server* says back.
        self.page.evaluate("document.querySelector('#train-panel form').noValidate = true")
        self.page.get_by_role("button", name="Log set").click()
        alert = self.page.locator("#train-panel [role=alert]")
        expect(alert).to_be_visible()
        expect(alert).to_contain_text("required")

    def test_a_failed_request_shows_an_error_toast(self):
        session, performed = self._session_with_set()
        self._log_in()
        self.page.goto(self._url(reverse("workouts:session-train", args=[session.pk])))
        self.page.route(
            "**" + reverse("workouts:train-set-log", args=[performed.pk]),
            lambda route: route.abort(),
        )
        self.page.get_by_role("button", name="Log set").click()
        toast = self.page.locator("#pr-toast-container .pr-banner-error")
        expect(toast).to_be_visible()
        expect(toast).to_contain_text("not saved")

    def test_a_save_shows_a_toast_and_the_field_partial_links_errors(self):
        self._log_in()
        self.page.goto(self._url(reverse("programs:program-create")))
        # Skip the browser's own `required` check to see the server's error rendering.
        self.page.evaluate("document.querySelector('form.card').noValidate = true")
        self.page.click("form.card button[type=submit]")  # empty name -> validation error
        name = self.page.locator("#id_name")
        expect(name).to_have_attribute("aria-invalid", "true")
        self.assertIn("id_name_error", name.get_attribute("aria-describedby"))
        name.fill("Leg day plan")
        self.page.click("form.card button[type=submit]")
        expect(self.page.locator("#pr-toast-container")).to_contain_text("Program created.")

    def test_breadcrumbs_orient_on_a_detail_page(self):
        program = Program.objects.create(owner=self.alice, name="Plan")
        self._log_in()
        self.page.goto(self._url(reverse("programs:program-detail", args=[program.pk])))
        crumbs = self.page.locator("nav[aria-label=Breadcrumb] li")
        expect(crumbs.last).to_have_attribute("aria-current", "page")

    # -- axe across more pages ------------------------------------------------
    def test_more_pages_have_no_serious_axe_violations(self):
        program = Program.objects.create(owner=self.alice, name="Plan")
        Workout.objects.create(program=program, name="Day A")
        session, _ = self._session_with_set()
        self._log_in()
        pages = {
            "Programs": reverse("programs:program-list"),
            "New program": reverse("programs:program-create"),
            "Program detail": reverse("programs:program-detail", args=[program.pk]),
            "Workout history": reverse("workouts:session-list"),
            "Training mode": reverse("workouts:session-train", args=[session.pk]),
            "Session detail": reverse("workouts:session-detail", args=[session.pk]),
            "Progress": reverse("analytics:dashboard"),
            "Body tracking": reverse("measurements:type-list"),
            "Activities": reverse("activities:type-list"),
            "Exercise detail": reverse("exercises:exercise-detail", args=[self.row.pk]),
        }
        for name, path in pages.items():
            self.page.goto(self._url(path))
            _assert_no_serious_violations(self, self.page, name, exclude=CALENDAR_OUTSIDE)

    def test_every_theme_in_light_and_dark_passes_axe(self):
        session, _ = self._session_with_set()
        self._log_in()
        for theme in Theme.values:
            for appearance in (Appearance.DARK, Appearance.LIGHT):
                User.objects.filter(pk=self.alice.pk).update(theme=theme, appearance=appearance)
                for name, path in {
                    "Dashboard": reverse("dashboard"),
                    "Training mode": reverse("workouts:session-train", args=[session.pk]),
                    "Profile": reverse("profile"),
                }.items():
                    self.page.goto(self._url(path))
                    _assert_no_serious_violations(
                        self,
                        self.page,
                        f"{name} ({theme}/{appearance})",
                        exclude=CALENDAR_OUTSIDE,
                    )

    def test_a_toast_banner_is_readable_in_every_theme(self):
        # The banners use --color-on-accent on --color-success; axe checks the
        # computed pair in each palette, light and dark.
        self._log_in()
        for theme in Theme.values:
            for appearance in (Appearance.DARK, Appearance.LIGHT):
                User.objects.filter(pk=self.alice.pk).update(theme=theme, appearance=appearance)
                self.page.goto(self._url(reverse("programs:program-create")))
                self.page.fill("#id_name", f"Plan {theme} {appearance}")
                self.page.click("form.card button[type=submit]")
                self.page.wait_for_selector("#pr-toast-container .pr-banner")
                _assert_no_serious_violations(
                    self, self.page, f"Toast ({theme}/{appearance})", exclude=CALENDAR_OUTSIDE
                )

    # -- responsive ------------------------------------------------------------
    def test_no_page_scrolls_sideways_at_any_common_width(self):
        """The audit's responsive pass: a horizontal page scrollbar at 320px is
        the classic sign of a desktop-first component (wide table, long
        unbroken text, fixed-width control). Tables scroll *inside* their own
        .table-wrap, so the document itself must never overflow."""
        program = Program.objects.create(owner=self.alice, name="A rather long program name " * 3)
        Workout.objects.create(program=program, name="Day A")
        session, _ = self._session_with_set()
        # Six nav items (with Nutrition) is the widest the top bar gets.
        User.objects.filter(pk=self.alice.pk).update(nutrition_enabled=True)
        self._log_in()
        pages = {
            "Dashboard": reverse("dashboard"),
            "Programs": reverse("programs:program-list"),
            "Program detail": reverse("programs:program-detail", args=[program.pk]),
            "Workout history": reverse("workouts:session-list"),
            "Training mode": reverse("workouts:session-train", args=[session.pk]),
            "Session detail": reverse("workouts:session-detail", args=[session.pk]),
            "Progress": reverse("analytics:dashboard"),
            "Exercises": reverse("exercises:exercise-list"),
            "Exercise detail": reverse("exercises:exercise-detail", args=[self.row.pk]),
            "Body tracking": reverse("measurements:type-list"),
            "Activities": reverse("activities:type-list"),
            "Profile": reverse("profile"),
        }
        for width in (320, 375, 768, 1024, 1440):
            self.page.set_viewport_size({"width": width, "height": 900})
            for name, path in pages.items():
                self.page.goto(self._url(path))
                overflow = self.page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                culprit = ""
                if overflow > 1:
                    culprit = self.page.evaluate(
                        """() => {
                            const w = document.documentElement.clientWidth;
                            const bad = [...document.querySelectorAll('body *')].filter(
                                e => e.getBoundingClientRect().right > w + 1
                            );
                            return bad.slice(0, 3).map(
                                e => e.tagName + '.' + e.className
                            ).join(' | ');
                        }"""
                    )
                self.assertLessEqual(
                    overflow, 1, f"{name} overflows by {overflow}px at {width}px: {culprit}"
                )
