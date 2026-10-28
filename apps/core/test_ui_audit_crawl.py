"""Whole-app UI audit crawl (docs/ui-ux/audit.md "Method"). A tool, not
a gate: it never fails on findings, only records them, so it is skipped
unless UI_AUDIT=1 is set (it takes ~10 minutes).

Seeds a realistic dataset, visits every page at five viewport widths and
writes overflow/layout/axe/console findings plus screenshots to
$UI_AUDIT_OUT (default /tmp/ui-audit). UI_AUDIT_ONLY=<substring> limits
it to matching URLs. Run it like the other browser tests
(apps/core/test_accessibility.py), adding `-n 0`.
"""

import datetime
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
pytest.importorskip("playwright", reason="requirements/a11y.txt not installed")

from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.activities.models import Activity, ActivityType  # noqa: E402
from apps.api import services as api_services  # noqa: E402
from apps.assistant.models import Conversation  # noqa: E402
from apps.core.models import Feedback  # noqa: E402
from apps.core.test_layout import CHECK_LAYOUT, LongContentLayoutTests  # noqa: E402
from apps.exercises.models import Exercise  # noqa: E402
from apps.measurements.models import BodyMeasurement, MeasurementType  # noqa: E402
from apps.nutrition.models import DietPlan, Food, Recipe  # noqa: E402
from apps.programs.models import Program  # noqa: E402
from apps.social import services as social  # noqa: E402
from apps.stretching import services as stretching  # noqa: E402
from apps.stretching.models import Stretch, StretchRoutine  # noqa: E402
from apps.workouts import services as workout_services  # noqa: E402
from apps.workouts.models import WorkoutSession  # noqa: E402

OUT = Path(os.environ.get("UI_AUDIT_OUT", "/tmp/ui-audit"))
VIEWPORTS = [320, 390, 768, 1280, 1680]
SHOT_AT = {390, 1280}
AXE = (Path(__file__).parent / "vendor" / "axe-core" / "axe.min.js").read_text()

PAGE_FACTS = (Path(__file__).parent / "ui_audit_facts.js").read_text()


@pytest.mark.accessibility
@pytest.mark.skipif(not os.environ.get("UI_AUDIT"), reason="set UI_AUDIT=1 to run the audit crawl")
class UiAuditCrawl(LongContentLayoutTests):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_user(
            username="audit",
            password="s3cret-pass",
            first_name="Audit",
            onboarding_completed=True,
            language="en",
            language_chosen=True,
            tutorials_enabled=False,
            is_personal_trainer=True,
            is_staff=True,
            is_superuser=True,
        )
        self.urls = self._seed() + self._seed_more()
        self.ctx = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(15000)
        self.addCleanup(self.ctx.close)
        self.console = []
        self.page.on(
            "console", lambda m: m.type in ("error", "warning") and self.console.append(m.text)
        )
        self.page.on("pageerror", lambda e: self.console.append("pageerror: " + str(e)))
        self.page.goto(f"{self.live_server_url}/accounts/login/")
        self.page.fill("input[name=username]", "audit")
        self.page.fill("input[name=password]", "s3cret-pass")
        self.page.click("button[type=submit]")
        self.page.wait_for_load_state("networkidle")

    def _seed_more(self):
        user = self.user
        from django.contrib.auth import get_user_model

        User = get_user_model()
        today = timezone.localdate()
        exercise = Exercise.objects.create(owner=user, name="Bench press")
        program = Program.objects.filter(owner=user).first()
        workout = program.workouts.first()
        done = workout_services.start_session(user, workout)
        pe = workout_services.add_performed_exercise(done, exercise)
        for i, w in enumerate((60, 70, 80)):
            workout_services.log_set(pe, weight=Decimal(w), reps=8 - i)
        workout_services.complete_session(done)
        mtype = MeasurementType.objects.create(owner=user, name="Body weight", unit_kind="weight")
        for d in range(6):
            BodyMeasurement.objects.create(
                user=user,
                measurement_type=mtype,
                value=Decimal(80 - d),
                recorded_at=timezone.now() - datetime.timedelta(days=d * 7),
            )
        atype = ActivityType.objects.create(owner=user, name="Running")
        act = Activity.objects.create(
            user=user,
            activity_type=atype,
            date=today,
            duration=datetime.timedelta(minutes=42),
            distance=Decimal("7.5"),
        )
        stretch = Stretch.objects.create(
            owner=user, name="Hamstring stretch", default_hold_seconds=30
        )
        routine = StretchRoutine.objects.filter(owner=user).first()
        stretching.add_routine_item(routine, stretch)
        sess = stretching.start_session(user, routine=routine)
        friend = User.objects.create_user(
            username="friend", password="x-s3cret-pass", onboarding_completed=True
        )
        req = social.send_friend_request(friend, user)
        social.accept_friend_request(req, acting_user=user)
        social.send_direct_message(friend, user, "Hi! Nice PR today.")
        social.send_direct_message(user, friend, "Thanks!")
        group = social.create_group(user, "Morning lifters", "Early birds")
        social.send_group_message(group, user, "Who's in tomorrow?")
        from apps.api.models import RateLimitTier

        RateLimitTier.objects.get_or_create(name="Default", defaults={"is_default": True})
        key, _raw = api_services.create_api_key(user, name="Home Assistant", permissions={})
        Feedback.objects.create(
            user=user, category="bug", subject="Test", message="Something broke"
        )
        conversation = Conversation.objects.filter(user=user).first()
        food = Food.objects.filter(owner=user).first()
        recipe = Recipe.objects.filter(owner=user).first()
        plan = DietPlan.objects.filter(user=user).first()
        entry = BodyMeasurement.objects.filter(user=user).first()
        s = pe.sets.first()
        open_session = WorkoutSession.objects.exclude(pk=done.pk).filter(user=user).first()
        return [
            reverse("profile"),
            reverse("account-details"),
            reverse("account-delete"),
            reverse("data-export"),
            reverse("password_change"),
            reverse("two-factor-setup"),
            reverse("two-factor-manage"),
            reverse("exercises:exercise-create"),
            reverse("exercises:exercise-update", args=[exercise.pk]),
            reverse("exercises:exercise-image-create", args=[exercise.pk]),
            reverse("exercises:exercise-detail", args=[exercise.pk]),
            reverse("programs:program-create"),
            reverse("programs:program-import"),
            reverse("programs:program-delete", args=[program.pk]),
            reverse("programs:workout-create", args=[program.pk]),
            reverse("programs:workout-update", args=[program.pk, workout.pk]),
            reverse("programs:prescription-create", args=[program.pk, workout.pk]),
            reverse("workouts:session-detail", args=[done.pk]),
            reverse("workouts:session-delete", args=[done.pk]),
            reverse("workouts:set-edit", args=[s.pk]),
            reverse("workouts:performed-exercise-add", args=[open_session.pk]),
            reverse("records:exercise-records", args=[exercise.pk]),
            reverse("measurements:type-create"),
            reverse("measurements:history", args=[mtype.pk]),
            reverse("measurements:log", args=[mtype.pk]),
            reverse("measurements:entry-edit", args=[entry.pk]),
            reverse("measurements:entry-delete", args=[entry.pk]),
            reverse("activities:type-create"),
            reverse("activities:history", args=[atype.pk]),
            reverse("activities:log", args=[atype.pk]),
            reverse("activities:entry-edit", args=[act.pk]),
            reverse("activities:entry-delete", args=[act.pk]),
            reverse("analytics:dashboard"),
            reverse("analytics:exercise", args=[exercise.pk]),
            reverse("analytics:member-profile", args=["friend"]),
            reverse("nutrition:goal-edit"),
            reverse("nutrition:onboarding-body"),
            reverse("nutrition:food-create"),
            reverse("nutrition:food-browse"),
            reverse("nutrition:food-import"),
            reverse("nutrition:diary-quick-add"),
            reverse("nutrition:recipe-create"),
            reverse("nutrition:recipe-import"),
            reverse("nutrition:recipe-update", args=[recipe.pk]),
            reverse("nutrition:recipe-delete", args=[recipe.pk]),
            reverse("nutrition:recipe-log", args=[recipe.pk]),
            reverse("nutrition:diet-plan-create"),
            reverse("nutrition:diet-plan-import"),
            reverse("nutrition:diet-plan-delete", args=[plan.pk]),
            reverse("nutrition:diet-plan-log", args=[plan.pk]),
            reverse("nutrition:shopping"),
            reverse("nutrition:calculator-bmr-tdee"),
            reverse("nutrition:calculator-macros"),
            reverse("nutrition:calculator-body-fat"),
            reverse("nutrition:calculator-water-intake"),
            reverse("nutrition:calculator-bmi"),
            reverse("nutrition:calculator-waist-hip-ratio"),
            reverse("nutrition:calculator-time-to-goal"),
            reverse("stretching:routine-list"),
            reverse("stretching:routine-create"),
            reverse("stretching:routine-update", args=[routine.pk]),
            reverse("stretching:routine-item-add", args=[routine.pk]),
            reverse("stretching:stretch-list"),
            reverse("stretching:stretch-create"),
            reverse("stretching:stretch-detail", args=[stretch.pk]),
            reverse("stretching:stretch-update", args=[stretch.pk]),
            reverse("stretching:session-history"),
            reverse("stretching:quick-log"),
            reverse("stretching:session-detail", args=[sess.pk]),
            reverse("stretching:session-play", args=[sess.pk]),
            reverse("api_keys:key-list"),
            reverse("api_keys:key-create"),
            reverse("api_keys:key-created", args=[key.pk]),
            reverse("social:friend-list"),
            reverse("social:friend-search"),
            reverse("social:block-list"),
            reverse("social:group-list"),
            reverse("social:group-create"),
            reverse("social:group-detail", args=[group.pk]),
            reverse("social:group-edit", args=[group.pk]),
            reverse("social:message-list"),
            reverse("social:message-thread", args=[friend.pk]),
            reverse("social:group-thread", args=[group.pk]),
            reverse("coaching:client-detail", args=["aino"]),
            reverse("coaching:my-coaches"),
            reverse("coaching:shared-plan-list"),
            reverse("tutorials:settings"),
            reverse("assistant:admin-settings"),
            reverse("backup-list"),
            reverse("feedback-create"),
            reverse("feedback-list"),
            reverse("seo-settings"),
            reverse("nutrition:food-detail", args=[food.pk]),
            reverse("assistant:conversation-detail", args=[conversation.pk]),
            "/this-page-does-not-exist/",
        ]

    test_long_content_stays_inside_and_rows_line_up = None

    def _axe(self, tags):
        self.page.evaluate(AXE)
        return self.page.evaluate(
            """([tags]) => axe.run(document, {runOnly: {type: 'tag', values: tags}})
                .then(r => r.violations.map(v => ({id: v.id, impact: v.impact, help: v.help,
                  nodes: v.nodes.slice(0, 5).map(
                    n => n.target.join(' ') + ' :: ' + (n.failureSummary || '').slice(0, 160)),
                  count: v.nodes.length})))""",
            [tags],
        )

    def _set_appearance(self, appearance):
        type(self.user).objects.filter(pk=self.user.pk).update(appearance=appearance)

    def test_crawl(self):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "shots").mkdir(exist_ok=True)
        urls = list(dict.fromkeys(self.urls + [reverse("login")]))
        if os.environ.get("UI_AUDIT_ONLY"):
            urls = [u for u in urls if os.environ["UI_AUDIT_ONLY"] in u]
        results = {}
        for appearance in ("dark", "light"):
            self._set_appearance(appearance)
            for url in urls:
                slug = url.strip("/").replace("/", "_") or "dashboard"
                entry = results.setdefault(url, {"viewports": {}})
                for width in VIEWPORTS if appearance == "dark" else [390]:
                    self.console.clear()
                    self.page.set_viewport_size({"width": width, "height": 900})
                    resp = self.page.goto(f"{self.live_server_url}{url}")
                    self.page.wait_for_load_state("networkidle")
                    vp = {"status": resp.status if resp else None, "final_url": self.page.url}
                    try:
                        vp["layout"] = self.page.evaluate(CHECK_LAYOUT)
                    except Exception as exc:  # pages without <main>
                        vp["layout"] = [["error", str(exc)[:100]]]
                    vp["scrollWidth"] = self.page.evaluate("document.documentElement.scrollWidth")
                    vp["console"] = list(self.console)
                    if width == 390:
                        vp["facts"] = self.page.evaluate(PAGE_FACTS)
                        vp["axe"] = self._axe(
                            [
                                "wcag2a",
                                "wcag2aa",
                                "wcag21a",
                                "wcag21aa",
                                "wcag22aa",
                                "best-practice",
                            ]
                        )
                    if width in SHOT_AT:
                        self.page.screenshot(
                            path=str(OUT / "shots" / f"{slug}--{width}-{appearance}.png"),
                            full_page=True,
                        )
                    if width == 390:
                        # What a phone really shows at the end of the page:
                        # full_page shots draw fixed nav/FABs at the first
                        # viewport's position, over unrelated content.
                        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        self.page.screenshot(
                            path=str(OUT / "shots" / f"{slug}--{width}-{appearance}-end.png")
                        )
                    entry["viewports"][f"{width}-{appearance}"] = vp
        (OUT / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
