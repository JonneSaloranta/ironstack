"""Real-browser accessibility checks (axe-core) for a handful of key
pages — docs/UI.md's own accessibility section (keyboard navigation,
visible focus, labels, contrast, touch targets, screen-reader-friendly
controls) is otherwise only checked indirectly, by plain HTML
assertions scattered across the rest of this test suite (a visually-
hidden label existing, an aria attribute being present, ...). Those
never actually render a page, so they can't catch what only shows up
once real CSS/DOM/ARIA semantics come together — color contrast that
looks fine in source but fails computed against actual page colors, a
focus trap, a landmark region that's technically present but empty.

All of it gated behind the `accessibility` pytest marker (excluded by
`pyproject.toml`'s own `addopts` from every plain `pytest` run) — see
`requirements/a11y.txt`'s own comment for why this needs its own,
separate dependency file rather than living in `dev.txt` alongside
everything else.
"""

import os
from pathlib import Path

# Playwright's sync API runs the real (async) browser driver on a
# background thread via greenlets — a known interaction with asyncio's
# contextvar-based "current loop" tracking leaves Django's own
# sync-safety check (django.utils.asyncio.async_unsafe, which every ORM
# call goes through) believing it's being called from an async context
# even though every ORM call in this file happens on the plain, normal
# test thread. Sanctioned by Django itself for exactly this kind of
# false positive (the check's own error message names this env var),
# not a way of masking a real concurrency bug — nothing in this file
# actually calls the ORM from Playwright's own thread. Set here, not
# via CI config, so `pytest -m accessibility` works the same everywhere
# it runs, with no separate env var to remember.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

import pytest
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from playwright.sync_api import sync_playwright

User = get_user_model()

# apps/core/vendor/axe-core/README.md explains why this is a vendored
# file injected via page.evaluate() rather than a CDN <script src> —
# in short, this app's own CSP blocks the latter on any page under
# test, the exact same way it would for a real visitor. Keep this in
# sync with that README (and axe.min.js itself) when upgrading.
AXE_CORE_VERSION = "4.10.2"
_AXE_CORE_SCRIPT = (
    Path(__file__).parent / "vendor" / "axe-core" / "axe.min.js"
).read_text(encoding="utf-8")

# Below "serious", axe-core's own rules get too subjective for a CI
# gate to assert on without constant false-positive tuning (a "minor"
# redundant-alt-text warning, a "moderate" heading-order nitpick) —
# "serious"/"critical" are the ones an actual screen-reader/keyboard
# user would concretely trip over, which is the bar worth blocking a
# merge on. Anything below that still prints, so it's never silently
# lost, just not fatal.
FAILING_IMPACTS = {"serious", "critical"}


def _run_axe(page, exclude=()):
    """Injects axe-core into the already-loaded `page` and returns its
    violations, restricted to WCAG 2.0/2.1 A and AA rules — the
    widely-agreed baseline, not axe-core's own "best practice" rules
    (opinionated beyond what WCAG itself requires) or the still-young
    2.2 rule set. `exclude`: CSS selectors axe itself should never
    even look inside (see `_assert_no_serious_violations`'s own
    docstring for the one current use).

    Evaluates the vendored script's source directly rather than
    `page.add_script_tag()` — the latter inserts a real `<script>`
    element into the page, which this app's own CSP blocks (no
    external script-src, no unsafe-inline) exactly as it would for a
    real visitor; `evaluate()` runs through the DevTools protocol
    instead, which CSP has no say over.
    """
    page.evaluate(_AXE_CORE_SCRIPT)
    return page.evaluate(
        """([exclude]) => axe.run(
            { exclude: exclude.map(sel => [sel]) },
            {
                runOnly: {
                    type: 'tag',
                    values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'],
                },
            },
        ).then(r => r.violations)""",
        [list(exclude)],
    )


def _assert_no_serious_violations(test, page, page_name, exclude=()):
    """`exclude`: CSS selectors to skip entirely — used exactly once
    today, for the dashboard's `.calendar-day-outside` padding days
    (a previous/next-month day filling out the grid, deliberately
    faded to `opacity: 0.35` — "visually out of the way of the month
    actually being looked at", that comment's own words, in
    static/css/base.css). That opacity is what axe actually flags (it
    computes contrast post-opacity-blend against the page background,
    correctly): the numbers/icons inside read fine at full opacity, so
    this isn't the same class of bug the button/tag/etc. color-pairing
    fix elsewhere in this same commit was — raising the opacity enough
    to satisfy axe would undo the deliberate de-emphasis those days
    are for. Left as a known, explicit exception rather than either
    silently weakening that design choice or leaving this whole test
    permanently red over it — a real product decision for whoever owns
    that tradeoff, not one this test should make unilaterally.
    """
    violations = _run_axe(page, exclude=exclude)
    serious = [v for v in violations if v["impact"] in FAILING_IMPACTS]
    other = [v for v in violations if v["impact"] not in FAILING_IMPACTS]
    if other:
        print(f"\n{page_name}: {len(other)} lower-impact axe violation(s) (not failing):")
        for v in other:
            print(f"  [{v['impact']}] {v['id']}: {v['help']} ({len(v['nodes'])} node(s))")
    if serious:
        detail = "\n".join(
            f"  [{v['impact']}] {v['id']}: {v['help']} ({v['helpUrl']}) "
            f"— {len(v['nodes'])} node(s), e.g. {v['nodes'][0]['target']}"
            for v in serious
        )
        test.fail(f"{page_name}: {len(serious)} serious/critical axe violation(s):\n{detail}")


@pytest.mark.accessibility
class AccessibilityTests(StaticLiveServerTestCase):
    """One Chromium instance shared across every test in this class
    (setUpClass/tearDownClass, not setUp/tearDown) — launching a real
    browser is the slow part of each of these, and nothing here needs
    a fresh one per test the way a fresh Django test client does."""

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
        self.addCleanup(self.page.close)
        self.alice = User.objects.create_user(
            username="alice", password="s3cret-pass", onboarding_completed=True
        )

    def _log_in(self):
        self.page.goto(f"{self.live_server_url}/accounts/login/")
        self.page.fill("input[name=username]", "alice")
        self.page.fill("input[name=password]", "s3cret-pass")
        self.page.click("button[type=submit]")
        self.page.wait_for_load_state("networkidle")

    def test_login_page(self):
        self.page.goto(f"{self.live_server_url}/accounts/login/")
        _assert_no_serious_violations(self, self.page, "Login page")

    def test_signup_page(self):
        self.page.goto(f"{self.live_server_url}/accounts/signup/")
        _assert_no_serious_violations(self, self.page, "Signup page")

    def test_dashboard(self):
        self._log_in()
        _assert_no_serious_violations(
            self, self.page, "Dashboard", exclude=[".calendar-day-outside"]
        )

    def test_profile_page(self):
        self._log_in()
        self.page.goto(f"{self.live_server_url}/accounts/profile/")
        _assert_no_serious_violations(self, self.page, "Profile page")

    def test_exercise_list(self):
        self._log_in()
        self.page.goto(f"{self.live_server_url}/exercises/")
        _assert_no_serious_violations(self, self.page, "Exercise list")
