import io
import json
import tarfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from django.views.defaults import permission_denied, server_error

from apps.core import backups as backup_services
from apps.core.bmi import BMI_CATEGORIES, calculate_bmi, category_for, category_rows
from apps.core.changelog import _render, render_changelog_html
from apps.core.charts import build_bar_series, build_chart_series
from apps.core.context_processors import app_version
from apps.core.greetings import _GREETINGS_BY_BUCKET, _time_bucket, random_greeting
from apps.core.models import BackupSettings, Feedback, FeedbackSettings
from apps.core.templatetags.core_extras import duration, translate_content
from apps.core.units import (
    cm_to_meters,
    inches_to_meters,
    kg_to_lb,
    km_to_meters,
    lb_to_kg,
    meters_to_cm,
    meters_to_inches,
    meters_to_km,
    meters_to_miles,
    miles_to_meters,
)
from apps.core.version import get_git_sha, get_migration_state, get_version


class UnitConversionTests(TestCase):
    def test_kg_to_lb_and_back_round_trips(self):
        kg = Decimal("100")
        lb = kg_to_lb(kg)
        self.assertEqual(lb, Decimal("220.46"))
        self.assertEqual(lb_to_kg(lb), Decimal("100.00"))

    def test_km_and_miles_round_trip(self):
        km = Decimal("5")
        self.assertEqual(meters_to_km(km_to_meters(km)), km)
        miles = meters_to_miles(Decimal("1609.344"))
        self.assertEqual(miles, Decimal("1.00"))
        self.assertEqual(miles_to_meters(miles), Decimal("1609.34"))

    def test_conversions_use_decimal_not_float(self):
        result = kg_to_lb(Decimal("82.5"))
        self.assertIsInstance(result, Decimal)

    def test_cm_round_trip_preserves_half_centimeter_precision(self):
        # A body circumference reported to the nearest half-cm must not be
        # rounded away by going through the meters canonical unit.
        cm = Decimal("85.5")
        meters = cm_to_meters(cm)
        self.assertEqual(meters, Decimal("0.8550"))
        self.assertEqual(meters_to_cm(meters), cm)

    def test_inches_round_trip(self):
        inches = Decimal("33.5")
        self.assertEqual(meters_to_inches(inches_to_meters(inches)), inches)


class BMICalculationTests(TestCase):
    def test_calculate_bmi_known_value(self):
        # 82.5 kg at 1.80 m -> a textbook example, ~25.5
        bmi = calculate_bmi(Decimal("82.5"), Decimal("1.80"))
        self.assertEqual(bmi, Decimal("25.5"))

    def test_missing_weight_or_height_returns_none(self):
        self.assertIsNone(calculate_bmi(None, Decimal("1.80")))
        self.assertIsNone(calculate_bmi(Decimal("82.5"), None))

    def test_non_positive_height_returns_none_rather_than_dividing_by_zero(self):
        self.assertIsNone(calculate_bmi(Decimal("82.5"), Decimal("0")))

    def test_category_for_covers_every_boundary(self):
        self.assertEqual(category_for(Decimal("18.4")).name, "Underweight")
        self.assertEqual(category_for(Decimal("18.5")).name, "Normal weight")
        self.assertEqual(category_for(Decimal("24.9")).name, "Normal weight")
        self.assertEqual(category_for(Decimal("25.0")).name, "Overweight")
        self.assertEqual(category_for(Decimal("29.9")).name, "Overweight")
        self.assertEqual(category_for(Decimal("30.0")).name, "Obese")

    def test_categories_are_contiguous_and_exhaustive(self):
        """Every category's high bound is the next one's low bound, and
        the whole range from 0 to infinity is covered — no gap a real
        BMI value could fall through uncategorized."""
        self.assertIsNone(BMI_CATEGORIES[0].low)
        self.assertIsNone(BMI_CATEGORIES[-1].high)
        for earlier, later in zip(BMI_CATEGORIES, BMI_CATEGORIES[1:]):
            self.assertEqual(earlier.high, later.low)

    def test_category_rows_convert_bmi_bounds_to_a_weight_range_at_a_given_height(self):
        rows = category_rows(Decimal("1.80"), "metric")
        normal = next(r for r in rows if r.category.name == "Normal weight")
        self.assertEqual(normal.weight_low, Decimal("59.9"))
        self.assertEqual(normal.weight_high, Decimal("81.0"))

    def test_category_rows_convert_to_the_users_display_unit(self):
        rows = category_rows(Decimal("1.80"), "imperial")
        normal = next(r for r in rows if r.category.name == "Normal weight")
        # 59.94 kg / 81.0 kg -> lb
        self.assertEqual(normal.weight_low, Decimal("132.2"))
        self.assertEqual(normal.weight_high, Decimal("178.6"))

    def test_open_ended_categories_have_one_none_weight_bound(self):
        rows = category_rows(Decimal("1.80"), "metric")
        underweight = next(r for r in rows if r.category.name == "Underweight")
        obese = next(r for r in rows if r.category.name == "Obese")
        self.assertIsNone(underweight.weight_low)
        self.assertIsNotNone(underweight.weight_high)
        self.assertIsNotNone(obese.weight_low)
        self.assertIsNone(obese.weight_high)

    def test_no_height_means_no_weight_bounds_at_all(self):
        rows = category_rows(None, "metric")
        self.assertTrue(all(r.weight_low is None and r.weight_high is None for r in rows))


class DurationFilterTests(TestCase):
    """Regression: a raw {{ some_timedelta }} rendered real seconds/
    microseconds from whenever a session was actually started/completed
    (e.g. "0:03:19.893476") — meaningless noise for a training-time
    stat. See apps.core.templatetags.core_extras.duration."""

    def test_none_renders_as_empty_string(self):
        self.assertEqual(duration(None), "")

    def test_minutes_only(self):
        self.assertEqual(duration(timedelta(minutes=45)), "45min")

    def test_hours_and_minutes(self):
        self.assertEqual(duration(timedelta(hours=1, minutes=15)), "1h 15min")

    def test_whole_hours_only(self):
        self.assertEqual(duration(timedelta(hours=2)), "2h")

    def test_rounds_to_the_nearest_minute_dropping_seconds_and_microseconds(self):
        messy = timedelta(hours=0, minutes=3, seconds=19, microseconds=893475)
        self.assertEqual(duration(messy), "3min")

    def test_seconds_round_up_into_the_next_minute_when_past_the_halfway_point(self):
        self.assertEqual(duration(timedelta(minutes=1, seconds=31)), "2min")

    def test_zero_duration(self):
        self.assertEqual(duration(timedelta()), "0min")


class TranslateContentFilterTests(TestCase):
    """Regression: Django's `{% trans someobj.name %}` tag doubles every
    "%" in a resolved template *variable* before the gettext lookup and
    undoes the doubling afterwards (django/templatetags/i18n.py's
    "Restore percent signs" step — meant for literal `%%` written by hand
    in template source, applied unconditionally to variables too), so a
    seeded content string containing a real "%" (e.g. MeasurementType
    "Body fat %") never matches its catalog entry and silently falls back
    to English. See apps.core.templatetags.core_extras.translate_content."""

    def test_translates_a_value_containing_a_percent_sign(self):
        with translation.override("fi"):
            self.assertEqual(translate_content("Body fat %"), "Rasvaprosentti")

    def test_translates_a_value_with_no_percent_sign_too(self):
        with translation.override("fi"):
            self.assertEqual(translate_content("Waist"), "Vyötärö")

    def test_falsy_value_passes_through_unchanged(self):
        self.assertEqual(translate_content(""), "")
        self.assertIsNone(translate_content(None))

    def test_content_never_extracted_into_the_catalog_renders_unchanged(self):
        with translation.override("fi"):
            self.assertEqual(translate_content("My Custom Thing"), "My Custom Thing")


class ChartSeriesServiceTests(TestCase):
    """apps.measurements and apps.activities both plot a value trend over
    time through this shared, model-agnostic utility — tested here once
    against plain (value, date) tuples rather than per-app model shapes."""

    def test_fewer_than_two_points_returns_no_series(self):
        now = timezone.now()
        self.assertIsNone(build_chart_series([]))
        self.assertIsNone(build_chart_series([(Decimal("80"), now)]))

    def test_series_is_ordered_chronologically_regardless_of_input_order(self):
        now = timezone.now()
        newer = (Decimal("82"), now)
        older = (Decimal("80"), now - timedelta(days=7))
        series = build_chart_series([newer, older])
        self.assertEqual([p.value for p in series.points], [Decimal("80"), Decimal("82")])

    def test_min_and_max_are_tracked(self):
        now = timezone.now()
        readings = [
            (Decimal("80"), now - timedelta(days=2)),
            (Decimal("85"), now - timedelta(days=1)),
            (Decimal("78"), now),
        ]
        series = build_chart_series(readings)
        self.assertEqual(series.min_value, Decimal("78"))
        self.assertEqual(series.max_value, Decimal("85"))

    def test_equal_values_do_not_divide_by_zero(self):
        now = timezone.now()
        readings = [(Decimal("80"), now - timedelta(days=1)), (Decimal("80"), now)]
        series = build_chart_series(readings)
        self.assertIsNotNone(series)
        self.assertEqual(series.points[0].y, series.points[1].y)

    def test_first_and_last_points_land_on_the_horizontal_padding(self):
        now = timezone.now()
        readings = [(Decimal("1"), now - timedelta(days=1)), (Decimal("2"), now)]
        series = build_chart_series(readings, width=600, padding=20)
        self.assertEqual(series.points[0].x, Decimal("20.00"))
        self.assertEqual(series.points[-1].x, Decimal("580.00"))


class BarSeriesServiceTests(TestCase):
    """apps.analytics plots category comparisons (weekly volume,
    muscle-group volume) through this shared, model-agnostic utility."""

    def test_no_categories_returns_no_series(self):
        self.assertIsNone(build_bar_series([]))

    def test_a_single_category_is_still_a_valid_series(self):
        series = build_bar_series([("Chest", Decimal("1000"))])
        self.assertIsNotNone(series)
        self.assertEqual(len(series.bars), 1)

    def test_bars_are_capped_at_the_max_thickness(self):
        # Few wide categories shouldn't produce bars that fill the slot.
        series = build_bar_series([("A", Decimal("1")), ("B", Decimal("2"))], width=600)
        for bar in series.bars:
            self.assertLessEqual(bar.width, Decimal("24"))

    def test_tallest_bar_reaches_the_full_plot_height(self):
        series = build_bar_series(
            [("A", Decimal("50")), ("B", Decimal("100"))], height=200, padding=20
        )
        tallest = series.bars[1]
        self.assertEqual(tallest.height, Decimal("160.00"))
        self.assertEqual(tallest.y, Decimal("20.00"))

    def test_all_zero_values_do_not_divide_by_zero(self):
        series = build_bar_series([("A", Decimal("0")), ("B", Decimal("0"))])
        self.assertIsNotNone(series)
        self.assertEqual(series.bars[0].height, Decimal("0.00"))

    def test_category_order_is_preserved_not_resorted(self):
        series = build_bar_series([("Z", Decimal("1")), ("A", Decimal("5"))])
        self.assertEqual([bar.label for bar in series.bars], ["Z", "A"])


class GreetingTests(TestCase):
    """Dashboard greeting: varied, time-of-day-aware, mixing
    encouragement and humor (apps.core.greetings) instead of a flat
    "Signed in as X" line — originally on the profile page, moved to
    the dashboard (Home)."""

    def test_time_bucket_boundaries(self):
        self.assertEqual(_time_bucket(4), "night")
        self.assertEqual(_time_bucket(5), "morning")
        self.assertEqual(_time_bucket(11), "morning")
        self.assertEqual(_time_bucket(12), "afternoon")
        self.assertEqual(_time_bucket(17), "afternoon")
        self.assertEqual(_time_bucket(18), "evening")
        self.assertEqual(_time_bucket(22), "evening")
        self.assertEqual(_time_bucket(23), "night")
        self.assertEqual(_time_bucket(0), "night")

    def test_greeting_substitutes_the_username(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="taylor", password="s3cret-pass")
        morning = timezone.datetime(2026, 1, 1, 8, 0, tzinfo=timezone.get_default_timezone())
        greeting = random_greeting(user, now=morning)
        self.assertIn("taylor", greeting)

    def test_greeting_is_drawn_from_the_bucket_matching_the_current_time(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="taylor", password="s3cret-pass")
        expected_untranslated = {str(t) for t in _GREETINGS_BY_BUCKET["night"]}
        late_night = timezone.datetime(2026, 1, 1, 1, 0, tzinfo=timezone.get_default_timezone())
        seen = set()
        for _i in range(30):
            greeting = random_greeting(user, now=late_night)
            seen.add(greeting.replace("taylor", "%(username)s"))
        self.assertTrue(seen.issubset(expected_untranslated))

    def test_greeting_varies_across_calls(self):
        """Not a strict guarantee (it's random), but with 4 candidates
        and 30 draws the odds of only ever hitting one are astronomically
        small — this exists to catch an accidental hardcoded pick, not
        to pin down exact randomness."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="taylor", password="s3cret-pass")
        morning = timezone.datetime(2026, 1, 1, 8, 0, tzinfo=timezone.get_default_timezone())
        seen = {random_greeting(user, now=morning) for _ in range(30)}
        self.assertGreater(len(seen), 1)

    def test_greeting_uses_the_first_name_when_set(self):
        """A user's own greeting always addresses them by first name if
        they've set one — unlike apps.analytics.achievements'
        public_display_name(), this isn't gated by
        User.show_name_to_others, since it's this user looking at their
        own dashboard, not something shown to anyone else."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(
            username="taylor",
            password="s3cret-pass",
            first_name="Taylor",
            show_name_to_others=False,
        )
        morning = timezone.datetime(2026, 1, 1, 8, 0, tzinfo=timezone.get_default_timezone())
        greeting = random_greeting(user, now=morning)
        self.assertIn("Taylor", greeting)
        self.assertNotIn("taylor", greeting)

    def test_greeting_falls_back_to_the_username_with_no_first_name(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="taylor", password="s3cret-pass")
        morning = timezone.datetime(2026, 1, 1, 8, 0, tzinfo=timezone.get_default_timezone())
        greeting = random_greeting(user, now=morning)
        self.assertIn("taylor", greeting)


class ErrorPageTests(TestCase):
    """Phase 11 polish: custom 404/403 pages instead of the bare Django/
    browser default, styled consistently with the rest of the app. Only
    rendered with DEBUG=False — Django shows its own debug page otherwise,
    which is what dev should see."""

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_404_uses_the_custom_template(self):
        response = self.client.get("/this-page-does-not-exist/")
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")
        self.assertContains(response, "Back to dashboard", status_code=404)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_403_uses_the_custom_template(self):
        request = RequestFactory().get("/")
        response = permission_denied(request, PermissionDenied())
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"permission", response.content.lower())

    def test_500_template_actually_renders(self):
        """Regression: templates/500.html used to explain, in a comment
        inside its <style> block, that it "can't rely on {% url %}" —
        but Django's template lexer scans the whole file for {% %}
        regardless of surrounding CSS/HTML comment syntax, so that
        literal "{% url %}" (with no arguments) was parsed as a real,
        broken tag. The custom error page crashed on every render — a
        real 500 would have shown Django's raw default error instead of
        this page, defeating the entire point of having one. Found by
        actually calling the handler, not just checking the file exists."""
        response = server_error(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Something went wrong", response.content)


class ContentSecurityPolicyTests(TestCase):
    """apps.core.middleware.ContentSecurityPolicyMiddleware — see its
    own docstring and docs/SECURITY.md for what each directive allows
    and why."""

    def test_header_is_present_on_a_plain_response(self):
        response = self.client.get(reverse("healthcheck"))
        self.assertIn("Content-Security-Policy", response)

    def test_default_src_is_locked_to_self(self):
        response = self.client.get(reverse("healthcheck"))
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])

    def test_framing_by_another_site_is_blocked(self):
        response = self.client.get(reverse("healthcheck"))
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])

    def test_img_src_allows_gravatar_alongside_self(self):
        # apps.accounts.models.User.gravatar_url — see docs/SECURITY.md
        # "Gravatar profile picture" for why this is the one deliberate
        # external allowance in an otherwise 'self'-only policy.
        response = self.client.get(reverse("healthcheck"))
        self.assertIn(
            "img-src 'self' data: https://www.gravatar.com",
            response["Content-Security-Policy"],
        )


class HealthcheckTests(TestCase):
    def test_healthcheck_returns_200_without_auth(self):
        response = self.client.get(reverse("healthcheck"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "ok")


class PWATests(TestCase):
    """Installable-PWA support: manifest + service worker, served at the
    site root (not /static/) so the service worker's scope covers the
    whole app — see apps.core.views._serve_static_root_file."""

    def test_manifest_is_served_at_the_root_without_auth(self):
        response = self.client.get("/manifest.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")

    def test_manifest_is_valid_json_naming_the_app_and_icons(self):
        import json

        response = self.client.get("/manifest.json")
        manifest = json.loads(response.content)
        self.assertEqual(manifest["name"], "IronStack")
        self.assertEqual(manifest["display"], "standalone")
        self.assertGreaterEqual(len(manifest["icons"]), 2)

    def test_service_worker_is_served_at_the_root_without_auth(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        # The scope-widening header matters as much as the 200 itself --
        # without it (or root placement), the worker's default scope
        # would only ever cover wherever it was served from.
        self.assertEqual(response["Service-Worker-Allowed"], "/")

    def test_service_worker_never_caches_non_static_requests(self):
        """The whole point: pages/forms/HTMX responses must never be
        served from a cache, or a stale "workout history" could be shown
        as if current (CLAUDE.md — historical trustworthiness)."""
        content = self.client.get("/sw.js").content.decode()
        self.assertIn('pathname.startsWith("/static/")', content)

    def test_static_assets_use_stale_while_revalidate_not_pure_cache_first(self):
        """Regression: a pure cache-first strategy served a cached
        CSS/JS asset forever once cached even once, never re-checking
        the network for it again — since static files aren't served at
        content-hashed URLs (no ManifestStaticFilesStorage), any later
        fix (e.g. a whole session's worth of chart/nav/layout CSS
        changes) was permanently invisible to a browser that had
        already cached the old version. A stale-while-revalidate
        fetch handler always refetches in the background (event.waitUntil
        keeps the worker alive for it) and updates the cache for next
        time, even when it serves the cached copy immediately."""
        content = self.client.get("/sw.js").content.decode()
        self.assertIn("event.waitUntil", content)
        self.assertIn("cache.put(event.request, response)", content)

    def test_base_page_links_the_manifest_and_registers_the_service_worker(self):
        """The registration call itself lives in static/js/sw-register.js
        (loaded via <script src>, not inline — see apps.core.middleware.
        ContentSecurityPolicyMiddleware's docstring for why an inline
        <script> block would just be silently blocked), fed the service
        worker's own URL through a data-* attribute on <body> rather than
        a template tag inside the script."""
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, '<link rel="manifest" href="/manifest.json">')
        self.assertContains(response, 'src="/static/js/sw-register.js"')
        self.assertContains(response, 'data-service-worker-url="/sw.js"')


class VersionTests(TestCase):
    """Single source of truth for the running IronStack version — a
    plain-text VERSION file at the repo root (apps.core.version), not
    hardcoded in Python, so it's readable by future non-Python tooling
    (a backup/restore script) too. Made available in every template via
    apps.core.context_processors.app_version, rendered today only in
    the profile page footer."""

    def test_get_version_reads_the_version_file(self):
        from django.conf import settings

        expected = (settings.BASE_DIR / "VERSION").read_text().strip()
        self.assertEqual(get_version(), expected)
        self.assertRegex(get_version(), r"^\d+\.\d+\.\d+$")

    def test_context_processor_exposes_app_version(self):
        request = RequestFactory().get("/")
        self.assertEqual(app_version(request), {"app_version": get_version()})

    def test_profile_page_shows_the_version_in_its_footer(self):
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("profile"))
        self.assertContains(response, 'class="app-version"')
        self.assertContains(response, get_version())

    def test_clicking_the_version_number_opens_a_changelog_modal(self):
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("profile"))
        self.assertContains(response, 'class="modal-backdrop"')
        self.assertContains(response, 'class="modal-body"')
        self.assertContains(response, "[1.0.0]")

    def test_get_git_sha_is_unknown_without_a_baked_in_file(self):
        """GIT_SHA is only ever written by scripts/build.sh's production
        build path (Dockerfile) — a dev container (or any plain
        `docker compose up -d --build`) has no such file."""
        get_git_sha.cache_clear()
        try:
            self.assertEqual(get_git_sha(), "unknown")
        finally:
            get_git_sha.cache_clear()

    def test_get_git_sha_reads_a_baked_in_file_when_present(self):
        get_git_sha.cache_clear()
        try:
            with TemporaryDirectory() as tmp:
                (Path(tmp) / "GIT_SHA").write_text("abc1234\n")
                with override_settings(BASE_DIR=Path(tmp)):
                    self.assertEqual(get_git_sha(), "abc1234")
        finally:
            get_git_sha.cache_clear()

    def test_get_migration_state_reports_the_latest_migration_per_app(self):
        state = get_migration_state()
        self.assertIn("accounts", state)
        self.assertIn("exercises", state)
        self.assertTrue(all(isinstance(name, str) for name in state.values()))

    def test_version_info_command_prints_a_json_blob_with_every_field(self):
        out = io.StringIO()
        call_command("version_info", stdout=out)
        data = json.loads(out.getvalue())
        self.assertEqual(data["version"], get_version())
        self.assertEqual(data["git_sha"], get_git_sha())
        self.assertIn("accounts", data["migrations"])
        self.assertIn("generated_at", data)

    def test_version_info_command_pretty_flag_indents_the_output(self):
        out = io.StringIO()
        call_command("version_info", "--pretty", stdout=out)
        self.assertIn("\n", out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["version"], get_version())


class ChangelogTests(TestCase):
    """apps.core.changelog — a narrowly-scoped Markdown-subset renderer
    for CHANGELOG.md specifically (not a general-purpose Markdown
    library — see that module's own docstring for why), powering the
    profile page's version-number modal."""

    def test_renders_the_real_changelog_file_without_error(self):
        html = render_changelog_html()
        self.assertIn("<h3>", html)
        self.assertIn("[1.0.0]", html)

    def test_headings(self):
        html = _render("# Title\n\n## [1.1.0] — 2026-01-01\n\n### Added\n- A thing\n")
        self.assertNotIn("Title", html)  # the top-level title is skipped
        self.assertIn("<h3>[1.1.0] — 2026-01-01</h3>", html)
        self.assertIn("<h4>Added</h4>", html)

    def test_bullets_including_a_soft_wrapped_continuation_line(self):
        html = _render("- First point\n  still the first point\n- Second point\n")
        self.assertIn("<li>First point still the first point</li>", html)
        self.assertIn("<li>Second point</li>", html)

    def test_a_blank_line_ends_a_list(self):
        html = _render("- One\n\nA plain paragraph.\n")
        self.assertIn("<ul><li>One</li></ul>", html)
        self.assertIn("<p>A plain paragraph.</p>", html)

    def test_inline_code_bold_and_links(self):
        html = _render("- Uses `apps.core` and **bold** and [a link](https://example.com).\n")
        self.assertIn("<code>apps.core</code>", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn(
            '<a href="https://example.com" target="_blank" rel="noopener">a link</a>', html
        )

    def test_html_in_the_source_is_escaped_not_executed(self):
        html = _render("- <script>alert(1)</script>\n")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_missing_file_returns_empty_string_rather_than_raising(self):
        from django.test import override_settings

        render_changelog_html.cache_clear()
        try:
            with override_settings(BASE_DIR=Path("/nonexistent-dir-for-testing")):
                self.assertEqual(render_changelog_html(), "")
        finally:
            render_changelog_html.cache_clear()


class BackupTests(TestCase):
    """apps.core.backups — the admin-only web-UI backup mechanism (see
    that module's own docstring for how and why it's a separate
    mechanism from scripts/backup.sh/restore.sh). The destructive
    restore_backup() is deliberately never called for real here — it
    drops and recreates the actual database via subprocess, which
    would corrupt this test run's own database. Its safe surface
    (create/list/download/manifest) is exercised for real against a
    temporary BACKUP_DIR; BackupViewTests below mocks restore_backup
    itself to verify the view wires it up correctly."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        patcher = mock.patch.object(backup_services, "BACKUP_DIR", Path(self.tmpdir.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_list_backups_is_empty_initially(self):
        self.assertEqual(backup_services.list_backups(), [])

    def test_create_backup_writes_a_real_archive_with_all_three_files(self):
        name = backup_services.create_backup()
        self.assertTrue(name.startswith("ironstack-backup-"))
        path = backup_services.BACKUP_DIR / name
        self.assertTrue(path.is_file())
        with tarfile.open(path, "r:gz") as tar:
            names = tar.getnames()
        self.assertIn("database.dump", names)
        self.assertIn("media.tar", names)
        self.assertIn("manifest.json", names)

    def test_list_backups_finds_a_created_backup(self):
        name = backup_services.create_backup()
        backups = backup_services.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["name"], name)
        self.assertGreater(backups[0]["size"], 0)

    def test_create_backup_defaults_to_manual_source(self):
        backup_services.create_backup()
        self.assertEqual(backup_services.list_backups()[0]["source"], "manual")

    def test_create_backup_records_a_custom_source(self):
        backup_services.create_backup(source="scheduled")
        self.assertEqual(backup_services.list_backups()[0]["source"], "scheduled")

    def test_list_backups_reports_version_and_git_sha_from_the_manifest(self):
        backup_services.create_backup()
        backup = backup_services.list_backups()[0]
        self.assertEqual(backup["version"], get_version())
        self.assertEqual(backup["git_sha"], get_git_sha())

    def test_list_backups_tags_an_uploaded_backup_as_uploaded(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for member_name, content in (
                ("database.dump", b"x"),
                ("media.tar", b"x"),
                ("manifest.json", json.dumps({"version": "9.9.9"}).encode()),
            ):
                data = io.BytesIO(content)
                info = tarfile.TarInfo(name=member_name)
                info.size = len(content)
                tar.addfile(info, data)
        buf.seek(0)
        backup_services.save_uploaded_backup(buf)
        backup = backup_services.list_backups()[0]
        self.assertEqual(backup["source"], "uploaded")
        self.assertIsNone(backup["version"])  # the uploaded archive's own manifest isn't read

    def test_delete_backup_removes_the_file(self):
        name = backup_services.create_backup()
        backup_services.delete_backup(name)
        self.assertEqual(backup_services.list_backups(), [])

    def test_delete_backup_rejects_an_invalid_name(self):
        with self.assertRaises(backup_services.InvalidBackupName):
            backup_services.delete_backup("../../etc/passwd")

    def test_read_manifest_returns_the_backups_own_version_info(self):
        name = backup_services.create_backup()
        manifest = backup_services.read_manifest(name)
        self.assertEqual(manifest["version"], get_version())
        self.assertIn("migrations", manifest)

    def test_safe_archive_path_rejects_path_traversal_and_dotted_names(self):
        for bad in ["../etc/passwd", "..\\evil", "/etc/passwd", ".", ".."]:
            with self.assertRaises(backup_services.InvalidBackupName):
                backup_services.safe_archive_path(bad)

    def test_safe_archive_path_rejects_a_name_that_doesnt_exist(self):
        with self.assertRaises(backup_services.InvalidBackupName):
            backup_services.safe_archive_path("does-not-exist.tar.gz")

    def test_safe_archive_path_accepts_a_real_backup(self):
        name = backup_services.create_backup()
        self.assertEqual(backup_services.safe_archive_path(name), backup_services.BACKUP_DIR / name)

    def test_create_backup_prunes_down_to_the_retention_setting(self):
        settings_row = BackupSettings.load()
        settings_row.retention_count = 2
        settings_row.save()
        for _ in range(4):
            backup_services.create_backup()
        self.assertEqual(len(backup_services.list_backups()), 2)

    def test_retention_count_zero_keeps_every_backup(self):
        settings_row = BackupSettings.load()
        settings_row.retention_count = 0
        settings_row.save()
        for _ in range(3):
            backup_services.create_backup()
        self.assertEqual(len(backup_services.list_backups()), 3)

    def test_prune_backups_keeps_the_newest(self):
        first = backup_services.create_backup()
        second = backup_services.create_backup()
        backup_services.prune_backups(1)
        remaining = [b["name"] for b in backup_services.list_backups()]
        self.assertEqual(remaining, [second])
        self.assertNotIn(first, remaining)

    def _build_valid_archive_bytes(self):
        """A minimal, real .tar.gz with all three members
        save_uploaded_backup() requires — same shape create_backup()
        itself writes, just built directly in memory rather than via a
        real pg_dump/tar of MEDIA_ROOT."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for member_name, content in (
                ("database.dump", b"fake dump"),
                ("media.tar", b"fake media"),
                ("manifest.json", json.dumps({"version": "9.9.9"}).encode()),
            ):
                data = io.BytesIO(content)
                info = tarfile.TarInfo(name=member_name)
                info.size = len(content)
                tar.addfile(info, data)
        buf.seek(0)
        return buf

    def test_save_uploaded_backup_accepts_a_valid_archive_and_names_it_uploaded(self):
        name = backup_services.save_uploaded_backup(self._build_valid_archive_bytes())
        self.assertTrue(name.startswith("ironstack-backup-uploaded-"))
        self.assertTrue((backup_services.BACKUP_DIR / name).is_file())

    def test_save_uploaded_backup_ignores_the_client_supplied_name(self):
        """The stored filename is always server-generated — never
        whatever the uploaded file object's own .name happens to be,
        the same "don't trust the request" reasoning safe_archive_path()
        applies elsewhere in this module."""
        upload = self._build_valid_archive_bytes()
        upload.name = "../../etc/passwd.tar.gz"
        name = backup_services.save_uploaded_backup(upload)
        self.assertNotIn("..", name)
        self.assertTrue(name.startswith("ironstack-backup-uploaded-"))

    def test_save_uploaded_backup_rejects_a_non_tar_file(self):
        with self.assertRaises(backup_services.InvalidBackupArchive):
            backup_services.save_uploaded_backup(io.BytesIO(b"not a tarball at all"))
        self.assertEqual(backup_services.list_backups(), [])

    def test_save_uploaded_backup_rejects_a_tar_missing_required_members(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = io.BytesIO(b"just this")
            info = tarfile.TarInfo(name="database.dump")
            info.size = len(b"just this")
            tar.addfile(info, data)
        buf.seek(0)
        with self.assertRaises(backup_services.InvalidBackupArchive):
            backup_services.save_uploaded_backup(buf)
        self.assertEqual(backup_services.list_backups(), [])

    def test_save_uploaded_backup_prunes_down_to_the_retention_setting(self):
        settings_row = BackupSettings.load()
        settings_row.retention_count = 1
        settings_row.save()
        backup_services.create_backup()
        backup_services.save_uploaded_backup(self._build_valid_archive_bytes())
        self.assertEqual(len(backup_services.list_backups()), 1)


class BackupSettingsModelTests(TestCase):
    """apps.core.models.BackupSettings — same admin-tunable singleton
    pattern as apps.api.models.ApiSettings."""

    def test_load_creates_the_singleton_with_defaults(self):
        settings_row = BackupSettings.load()
        self.assertTrue(settings_row.enabled)
        self.assertEqual(settings_row.retention_count, 14)

    def test_default_hour_comes_from_the_backup_hour_setting(self):
        with override_settings(BACKUP_HOUR=7):
            settings_row = BackupSettings.load()
        self.assertEqual(settings_row.hour, 7)

    def test_load_always_returns_the_same_row(self):
        first = BackupSettings.load()
        first.retention_count = 30
        first.save()
        second = BackupSettings.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.retention_count, 30)

    def test_save_always_targets_pk_1_even_for_a_fresh_instance(self):
        settings_row = BackupSettings(enabled=False, hour=9, retention_count=5)
        settings_row.save()
        self.assertEqual(settings_row.pk, 1)
        self.assertEqual(BackupSettings.objects.count(), 1)

    def test_delete_is_a_no_op(self):
        settings_row = BackupSettings.load()
        settings_row.delete()
        self.assertTrue(BackupSettings.objects.filter(pk=1).exists())


class BackupManagementCommandTests(TestCase):
    """The CLI entry points docs/BACKUP.md's automatic backups
    (docker-compose.yml's `backup-scheduler` service, or a host cron
    entry) actually call — apps.core.management.commands.create_backup
    and .backup_scheduler."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        patcher = mock.patch.object(backup_services, "BACKUP_DIR", Path(self.tmpdir.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_create_backup_command_writes_a_real_archive(self):
        out = io.StringIO()
        call_command("create_backup", stdout=out)
        self.assertIn("Backup created:", out.getvalue())
        self.assertEqual(len(backup_services.list_backups()), 1)

    def test_seconds_until_a_hour_later_today(self):
        from apps.core.management.commands.backup_scheduler import _seconds_until

        noon = timezone.datetime(2026, 1, 1, 12, 0, tzinfo=timezone.get_default_timezone())
        with mock.patch("django.utils.timezone.now", return_value=noon):
            self.assertEqual(_seconds_until(15), 3 * 3600)

    def test_seconds_until_wraps_to_tomorrow_once_the_hour_has_passed_today(self):
        from apps.core.management.commands.backup_scheduler import _seconds_until

        noon = timezone.datetime(2026, 1, 1, 12, 0, tzinfo=timezone.get_default_timezone())
        with mock.patch("django.utils.timezone.now", return_value=noon):
            self.assertEqual(_seconds_until(3), 15 * 3600)

    def test_backup_scheduler_creates_a_backup_each_time_it_wakes_up(self):
        """The real command loops forever — this exercises exactly one
        wake-up by letting the first time.sleep() succeed (so handle()
        proceeds to call create_backup) and making the *second* one
        raise, standing in for "the process is being shut down"."""
        from apps.core.management.commands.backup_scheduler import Command

        sleep_calls = {"count": 0}

        def fake_sleep(_seconds):
            sleep_calls["count"] += 1
            if sleep_calls["count"] >= 2:
                raise KeyboardInterrupt

        with (
            mock.patch(
                "apps.core.management.commands.backup_scheduler.time.sleep",
                side_effect=fake_sleep,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            Command().handle()
        self.assertEqual(len(backup_services.list_backups()), 1)

    def test_backup_scheduler_tags_its_own_backups_as_scheduled(self):
        """Profile → Administration → Backups tags each backup by who/
        what made it — the scheduler is the only built-in caller that
        passes --source scheduled to the create_backup management
        command (apps.core.backups.create_backup's own docstring)."""
        from apps.core.management.commands.backup_scheduler import Command

        sleep_calls = {"count": 0}

        def fake_sleep(_seconds):
            sleep_calls["count"] += 1
            if sleep_calls["count"] >= 2:
                raise KeyboardInterrupt

        with (
            mock.patch(
                "apps.core.management.commands.backup_scheduler.time.sleep",
                side_effect=fake_sleep,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            Command().handle()
        self.assertEqual(backup_services.list_backups()[0]["source"], "scheduled")

    def test_backup_scheduler_skips_creating_a_backup_when_disabled(self):
        """The "Automatic daily backups" toggle (Profile → Administration
        → Backups) — checked fresh on every wake-up, not just once at
        process startup, so flipping it takes effect without restarting
        this container."""
        from apps.core.management.commands.backup_scheduler import Command

        settings_row = BackupSettings.load()
        settings_row.enabled = False
        settings_row.save()

        sleep_calls = {"count": 0}

        def fake_sleep(_seconds):
            sleep_calls["count"] += 1
            if sleep_calls["count"] >= 1:
                raise KeyboardInterrupt

        with (
            mock.patch(
                "apps.core.management.commands.backup_scheduler.time.sleep",
                side_effect=fake_sleep,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            Command().handle()
        self.assertEqual(len(backup_services.list_backups()), 0)

    def test_backup_scheduler_reads_the_hour_from_backup_settings_not_the_static_setting(self):
        from apps.core.management.commands.backup_scheduler import Command

        settings_row = BackupSettings.load()
        settings_row.hour = 9
        settings_row.save()

        noon = timezone.datetime(2026, 1, 1, 12, 0, tzinfo=timezone.get_default_timezone())
        with (
            mock.patch("django.utils.timezone.now", return_value=noon),
            mock.patch(
                "apps.core.management.commands.backup_scheduler.time.sleep",
                side_effect=KeyboardInterrupt,
            ) as mock_sleep,
            self.assertRaises(KeyboardInterrupt),
        ):
            Command().handle()

        # From "noon", the next 09:00 is tomorrow (today's has already
        # passed) — 21 hours away. settings.BACKUP_HOUR's own default
        # (3) would instead be 15 hours away from the same "noon", so
        # this also confirms BackupSettings.hour is what's actually
        # read, not the static setting.
        mock_sleep.assert_called_once_with(21 * 3600)

    def test_backup_scheduler_survives_a_failed_backup_attempt(self):
        """A single failed backup must not take the whole scheduler
        process down — otherwise one transient failure (e.g. the
        database briefly unreachable) silently stops every future
        scheduled backup until someone notices and restarts it."""
        from apps.core.management.commands.backup_scheduler import Command

        sleep_calls = {"count": 0}

        def fake_sleep(_seconds):
            sleep_calls["count"] += 1
            if sleep_calls["count"] >= 2:
                raise KeyboardInterrupt

        with (
            mock.patch(
                "apps.core.management.commands.backup_scheduler.time.sleep",
                side_effect=fake_sleep,
            ),
            mock.patch.object(backup_services, "create_backup", side_effect=RuntimeError("boom")),
            self.assertRaises(KeyboardInterrupt),
        ):
            Command().handle()
        self.assertEqual(sleep_calls["count"], 2)  # looped again instead of dying


class BackupViewTests(TestCase):
    """Admin-only backup management linked from the profile page."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        patcher = mock.patch.object(backup_services, "BACKUP_DIR", Path(self.tmpdir.name))
        patcher.start()
        self.addCleanup(patcher.stop)

        User = get_user_model()
        self.admin = User.objects.create_user(
            username="admin", password="s3cret-pass", is_staff=True
        )
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_list_view_requires_login(self):
        response = self.client.get(reverse("backup-list"))
        self.assertEqual(response.status_code, 302)

    def test_list_view_requires_staff(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("backup-list"))
        self.assertEqual(response.status_code, 403)

    def test_list_view_shows_backups_to_staff(self):
        self.client.login(username="admin", password="s3cret-pass")
        name = backup_services.create_backup()
        response = self.client.get(reverse("backup-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, name)

    def test_list_view_shows_only_the_newest_of_several_scheduled_backups(self):
        """Older scheduled backups are handed to the template
        separately (older_scheduled_backups), collapsed behind a
        toggle, rather than cluttering the automatic-backups section —
        see BackupListView.get_context_data's own docstring/comment."""
        backup_services.create_backup(source="scheduled")
        newest = backup_services.create_backup(source="scheduled")
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.get(reverse("backup-list"))
        self.assertEqual(response.context["newest_scheduled"]["name"], newest)
        self.assertEqual(len(response.context["older_scheduled_backups"]), 1)

    def test_list_view_shows_manual_and_uploaded_backups_individually(self):
        """Manual/uploaded backups get their own section from
        automatic ones, and never collapse within it — each was a
        deliberate, individual action."""
        backup_services.create_backup(source="manual")
        backup_services.create_backup(source="manual")
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.get(reverse("backup-list"))
        self.assertEqual(len(response.context["manual_backups"]), 2)
        self.assertIsNone(response.context["newest_scheduled"])
        self.assertEqual(response.context["older_scheduled_backups"], [])

    def test_posting_delete_removes_the_backup_and_redirects(self):
        self.client.login(username="admin", password="s3cret-pass")
        name = backup_services.create_backup()
        response = self.client.post(reverse("backup-delete", args=[name]))
        self.assertRedirects(response, reverse("backup-list"))
        self.assertEqual(backup_services.list_backups(), [])

    def test_delete_requires_staff(self):
        self.client.login(username="alice", password="s3cret-pass")
        name = backup_services.create_backup()
        response = self.client.post(reverse("backup-delete", args=[name]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(backup_services.list_backups()), 1)

    def test_delete_404s_for_a_nonexistent_backup(self):
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.post(reverse("backup-delete", args=["nope.tar.gz"]))
        self.assertEqual(response.status_code, 404)

    def test_posting_to_list_view_creates_a_backup_and_redirects(self):
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.post(reverse("backup-list"))
        self.assertRedirects(response, reverse("backup-list"))
        self.assertEqual(len(backup_services.list_backups()), 1)

    def _build_valid_upload(self, name="my-download.tar.gz"):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for member_name, content in (
                ("database.dump", b"fake dump"),
                ("media.tar", b"fake media"),
                ("manifest.json", json.dumps({"version": "9.9.9"}).encode()),
            ):
                data = io.BytesIO(content)
                info = tarfile.TarInfo(name=member_name)
                info.size = len(content)
                tar.addfile(info, data)
        return SimpleUploadedFile(name, buf.getvalue(), content_type="application/gzip")

    def test_uploading_a_valid_backup_redirects_straight_to_its_restore_confirm_page(self):
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.post(
            reverse("backup-list"),
            {"action": "upload_backup", "archive": self._build_valid_upload()},
        )
        backups = backup_services.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertRedirects(response, reverse("backup-restore", args=[backups[0]["name"]]))

    def test_uploading_a_non_tar_gz_filename_shows_a_form_error_without_saving_anything(self):
        self.client.login(username="admin", password="s3cret-pass")
        upload = SimpleUploadedFile("not-a-backup.txt", b"hello", content_type="text/plain")
        response = self.client.post(
            reverse("backup-list"), {"action": "upload_backup", "archive": upload}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "field-error")
        self.assertEqual(backup_services.list_backups(), [])

    def test_uploading_a_tar_gz_missing_required_members_shows_a_form_error(self):
        self.client.login(username="admin", password="s3cret-pass")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = io.BytesIO(b"just this")
            info = tarfile.TarInfo(name="database.dump")
            info.size = len(b"just this")
            tar.addfile(info, data)
        upload = SimpleUploadedFile(
            "incomplete.tar.gz", buf.getvalue(), content_type="application/gzip"
        )
        response = self.client.post(
            reverse("backup-list"), {"action": "upload_backup", "archive": upload}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "field-error")
        self.assertEqual(backup_services.list_backups(), [])

    def test_upload_requires_staff(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.post(
            reverse("backup-list"),
            {"action": "upload_backup", "archive": self._build_valid_upload()},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(backup_services.list_backups(), [])

    def test_list_view_shows_the_settings_form_prefilled_with_current_values(self):
        settings_row = BackupSettings.load()
        settings_row.hour = 4
        settings_row.retention_count = 21
        settings_row.save()
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.get(reverse("backup-list"))
        self.assertContains(response, 'value="4"')
        self.assertContains(response, 'value="21"')

    def test_posting_save_settings_updates_backup_settings_and_redirects(self):
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.post(
            reverse("backup-list"),
            {"action": "save_settings", "hour": "6", "retention_count": "5"},
            # "enabled" omitted — an unchecked checkbox isn't sent.
        )
        self.assertRedirects(response, reverse("backup-list"))
        settings_row = BackupSettings.load()
        self.assertFalse(settings_row.enabled)
        self.assertEqual(settings_row.hour, 6)
        self.assertEqual(settings_row.retention_count, 5)

    def test_posting_save_settings_does_not_create_a_backup(self):
        self.client.login(username="admin", password="s3cret-pass")
        self.client.post(
            reverse("backup-list"),
            {"action": "save_settings", "enabled": "on", "hour": "6", "retention_count": "5"},
        )
        self.assertEqual(backup_services.list_backups(), [])

    def test_posting_save_settings_with_an_invalid_hour_re_renders_with_an_error(self):
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.post(
            reverse("backup-list"),
            {"action": "save_settings", "enabled": "on", "hour": "24", "retention_count": "5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "field-error")
        settings_row = BackupSettings.load()
        self.assertEqual(settings_row.hour, 3)  # default untouched

    def test_settings_form_requires_staff(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.post(
            reverse("backup-list"),
            {"action": "save_settings", "hour": "6", "retention_count": "5"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(BackupSettings.load().hour, 3)  # default untouched

    def test_download_view_streams_the_archive(self):
        self.client.login(username="admin", password="s3cret-pass")
        name = backup_services.create_backup()
        response = self.client.get(reverse("backup-download", args=[name]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(name, response["Content-Disposition"])

    def test_download_view_requires_staff(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("backup-download", args=["nope.tar.gz"]))
        self.assertEqual(response.status_code, 403)

    def test_download_view_404s_for_a_nonexistent_backup(self):
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.get(reverse("backup-download", args=["nope.tar.gz"]))
        self.assertEqual(response.status_code, 404)

    def test_restore_confirm_view_shows_both_manifests(self):
        self.client.login(username="admin", password="s3cret-pass")
        name = backup_services.create_backup()
        response = self.client.get(reverse("backup-restore", args=[name]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, get_version())

    def test_restore_confirm_view_requires_staff(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("backup-restore", args=["nope.tar.gz"]))
        self.assertEqual(response.status_code, 403)

    def test_posting_restore_calls_restore_backup_and_redirects_to_profile(self):
        self.client.login(username="admin", password="s3cret-pass")
        name = backup_services.create_backup()
        with mock.patch.object(backup_services, "restore_backup") as mock_restore:
            response = self.client.post(reverse("backup-restore", args=[name]))
        mock_restore.assert_called_once_with(name)
        self.assertRedirects(response, reverse("profile"))


class FeedbackSettingsModelTests(TestCase):
    """apps.core.models.FeedbackSettings — same admin-tunable singleton
    pattern as BackupSettings/apps.api.models.ApiSettings."""

    def test_load_creates_the_singleton_enabled_by_default(self):
        self.assertTrue(FeedbackSettings.load().enabled)

    def test_load_always_returns_the_same_row(self):
        first = FeedbackSettings.load()
        first.enabled = False
        first.save()
        second = FeedbackSettings.load()
        self.assertEqual(first.pk, second.pk)
        self.assertFalse(second.enabled)

    def test_save_always_targets_pk_1_even_for_a_fresh_instance(self):
        settings_row = FeedbackSettings(enabled=False)
        settings_row.save()
        self.assertEqual(settings_row.pk, 1)
        self.assertEqual(FeedbackSettings.objects.count(), 1)

    def test_delete_is_a_no_op(self):
        settings_row = FeedbackSettings.load()
        settings_row.delete()
        self.assertTrue(FeedbackSettings.objects.filter(pk=1).exists())


class FeedbackModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_str_combines_category_and_subject(self):
        feedback = Feedback.objects.create(
            user=self.alice,
            category=Feedback.Category.PROGRESS,
            subject="Chart is confusing",
            message="The y-axis doesn't say what unit it's in.",
        )
        self.assertEqual(str(feedback), "Progress: Chart is confusing")

    def test_ordering_is_newest_first(self):
        older = Feedback.objects.create(
            user=self.alice, category=Feedback.Category.OTHER, subject="First", message="..."
        )
        newer = Feedback.objects.create(
            user=self.alice, category=Feedback.Category.OTHER, subject="Second", message="..."
        )
        self.assertEqual(list(Feedback.objects.all()), [newer, older])


class FeedbackViewTests(TestCase):
    """Profile → Feedback (any signed-in user) and Profile →
    Administration → Feedback (staff only)."""

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="admin", password="s3cret-pass", is_staff=True
        )
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_create_view_requires_login(self):
        response = self.client.get(reverse("feedback-create"))
        self.assertEqual(response.status_code, 302)

    def test_any_signed_in_user_can_submit_feedback(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.post(
            reverse("feedback-create"),
            {
                "category": Feedback.Category.PROGRESS,
                "subject": "Chart is confusing",
                "message": "The y-axis doesn't say what unit it's in.",
            },
        )
        self.assertRedirects(response, reverse("profile"))
        feedback = Feedback.objects.get()
        self.assertEqual(feedback.user, self.alice)
        self.assertEqual(feedback.category, Feedback.Category.PROGRESS)
        self.assertEqual(feedback.subject, "Chart is confusing")

    def test_create_view_is_gated_by_feedback_settings(self):
        settings_row = FeedbackSettings.load()
        settings_row.enabled = False
        settings_row.save()
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.post(
            reverse("feedback-create"),
            {"category": Feedback.Category.OTHER, "subject": "x", "message": "y"},
        )
        self.assertRedirects(response, reverse("profile"))
        self.assertEqual(Feedback.objects.count(), 0)

    def test_feedback_card_hidden_from_profile_when_disabled(self):
        settings_row = FeedbackSettings.load()
        settings_row.enabled = False
        settings_row.save()
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("profile"))
        self.assertNotContains(response, reverse("feedback-create"))

    def test_list_view_requires_login(self):
        response = self.client.get(reverse("feedback-list"))
        self.assertEqual(response.status_code, 302)

    def test_list_view_requires_staff(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("feedback-list"))
        self.assertEqual(response.status_code, 403)

    def test_list_view_shows_submitted_feedback_to_staff(self):
        Feedback.objects.create(
            user=self.alice,
            category=Feedback.Category.OTHER,
            subject="A wild subject",
            message="A wild message",
        )
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.get(reverse("feedback-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A wild subject")
        self.assertContains(response, "A wild message")

    def test_a_regular_user_never_sees_another_users_feedback(self):
        Feedback.objects.create(
            user=self.admin,
            category=Feedback.Category.OTHER,
            subject="Admin-only subject",
            message="...",
        )
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("feedback-list"))
        self.assertEqual(response.status_code, 403)

    def test_posting_save_settings_updates_feedback_settings_and_redirects(self):
        self.client.login(username="admin", password="s3cret-pass")
        response = self.client.post(
            reverse("feedback-list"),
            {"action": "save_settings"},  # "enabled" omitted — unchecked checkbox isn't sent
        )
        self.assertRedirects(response, reverse("feedback-list"))
        self.assertFalse(FeedbackSettings.load().enabled)

    def test_settings_form_requires_staff(self):
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.post(reverse("feedback-list"), {"action": "save_settings"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(FeedbackSettings.load().enabled)  # default untouched


class DashboardAccessTests(TestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


class AdminThemeTests(TestCase):
    """Django's own admin, re-themed to match IronStack rather than a
    hand-built parallel admin page (apps.core.admin,
    templates/admin/base_site.html, static/css/admin_theme.css) — see
    that CSS file's own comment for the reasoning."""

    def test_branding_is_ironstack_not_django(self):
        response = self.client.get(reverse("admin:login"))
        self.assertContains(response, "IronStack")
        self.assertNotContains(response, "Django administration")

    def test_theme_css_is_linked(self):
        response = self.client.get(reverse("admin:login"))
        self.assertContains(response, "admin_theme.css")

    def test_theme_css_file_exists_and_overrides_admin_variables(self):
        from django.conf import settings

        content = (settings.BASE_DIR / "static" / "css" / "admin_theme.css").read_text()
        self.assertIn("--body-bg", content)


class BottomNavTests(TestCase):
    """Mobile nav: Home, Progress, Workout, Programs, Profile in that
    order, icon-only on mobile — each link's accessible name comes from
    aria-label since the text label is visually hidden at that width
    (re-shown alongside the icon on desktop)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_nav_order_and_labels(self):
        response = self.client.get(reverse("dashboard"))
        content = response.content.decode()
        positions = [content.find(f'aria-label="{label}"') for label in
                     ["Home", "Progress", "Workout", "Programs", "Profile"]]
        self.assertTrue(all(p != -1 for p in positions), positions)
        self.assertEqual(positions, sorted(positions))

    def test_every_nav_link_has_an_icon(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "nav-icon", count=5)

    def test_nav_hidden_for_anonymous_users(self):
        self.client.logout()
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, 'class="bottom-nav"')

    def test_progress_nav_link_points_to_the_analytics_dashboard(self):
        """Regression: "Progress" used to link to Body tracking (measurements),
        not the actual training-volume/PR/strength-trend analytics page —
        a mismatch between the label and where it actually led."""
        response = self.client.get(reverse("dashboard"))
        self.assertContains(
            response, f'href="{reverse("analytics:dashboard")}" aria-label="Progress"'
        )

    def test_only_progress_is_current_on_the_analytics_dashboard(self):
        """Regression: both "dashboard" (core) and "analytics:dashboard"
        share the bare url_name "dashboard", so a naive
        request.resolver_match.url_name == 'dashboard' check on the Home
        link also matched while viewing Progress — lighting up both nav
        items at once. Must key off the namespaced view_name instead."""
        response = self.client.get(reverse("analytics:dashboard"))
        content = response.content.decode()
        self.assertNotIn('aria-current="page"', self._nav_tag(content, "Home"))
        self.assertIn('aria-current="page"', self._nav_tag(content, "Progress"))

    def test_home_is_current_on_the_dashboard(self):
        response = self.client.get(reverse("dashboard"))
        content = response.content.decode()
        self.assertIn('aria-current="page"', self._nav_tag(content, "Home"))

    @staticmethod
    def _nav_tag(content, label):
        """The full opening <a ...> tag for the nav item with this
        aria-label, so aria-current can be asserted present/absent on
        the right link specifically rather than anywhere on the page."""
        label_pos = content.find(f'aria-label="{label}"')
        tag_start = content.rfind("<a", 0, label_pos)
        tag_end = content.find(">", label_pos)
        return content[tag_start : tag_end + 1]


class DashboardWidgetsTests(TestCase):
    """docs/UI.md dashboard content: this week's workouts/volume, recent
    PRs, body weight — see apps.core.views.DashboardView."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.User = get_user_model()
        self.alice = self.User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_dashboard_renders_with_no_history(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["week_summary"].session_count, 0)
        self.assertIsNone(response.context["body_weight"])

    def test_dashboard_shows_a_greeting(self):
        """Regression: the varied, time-of-day-aware greeting
        (apps.core.greetings) originally opened the profile page — moved
        here instead, since Home is the page a user actually lands on."""
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'class="dashboard-greeting"')
        self.assertIn("alice", response.context["greeting"])

    def test_this_weeks_workout_count_is_shown_not_just_computed(self):
        """Regression: week_summary.session_count was already computed
        into the context but never rendered anywhere on the page."""
        from datetime import timedelta

        from apps.exercises.models import Exercise
        from apps.workouts import services as workout_services

        exercise = Exercise.objects.create(name="Test Row", owner=None)
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, exercise)
        workout_services.log_set(performed, weight=Decimal("40"), reps=10)
        workout_services.complete_session(session)
        session.ended_at = session.started_at + timedelta(minutes=20)
        session.save(update_fields=["ended_at"])

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["week_summary"].session_count, 1)
        self.assertContains(response, "This week's workouts")

    def test_body_weight_widget_shows_the_latest_reading_in_display_units(self):
        from decimal import Decimal

        from apps.measurements.models import BodyMeasurement, MeasurementType

        body_weight_type = MeasurementType.objects.get(name="Body weight", owner=None)
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=body_weight_type, value=Decimal("82.5")
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["body_weight"], Decimal("82.50"))
        self.assertContains(response, "82.50 kg")

    def test_dashboard_does_not_duplicate_main_nav_destinations(self):
        """Regression: the dashboard used to carry its own "Analytics",
        "Workout history", and "Programs" cards that led to the exact
        same pages the bottom nav already links to — each URL should now
        appear exactly once on the page (from the nav)."""
        response = self.client.get(reverse("dashboard"))
        content = response.content.decode()
        for url_name in ["workouts:session-list", "programs:program-list", "analytics:dashboard"]:
            url = reverse(url_name)
            self.assertEqual(
                content.count(url), 1, f"{url_name} ({url}) should appear only once, in the nav"
            )

    def test_dashboard_has_no_logout_button(self):
        """Regression: the dashboard used to duplicate the logout button
        already available on the profile page."""
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, reverse("logout"))

    def test_recent_prs_widget_only_shows_the_logged_in_users_own_prs(self):
        from decimal import Decimal

        from django.utils import timezone

        from apps.exercises.models import Exercise
        from apps.records.models import PersonalRecord, PRType

        bob = self.User.objects.create_user(username="bob", password="s3cret-pass")
        exercise = Exercise.objects.create(name="Test Snatch", owner=None)
        PersonalRecord.objects.create(
            user=bob,
            exercise=exercise,
            record_type=PRType.MAX_WEIGHT,
            value=Decimal("999"),
            weight=Decimal("999"),
            reps=1,
            achieved_at=timezone.now(),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(list(response.context["recent_prs"]), [])


class AchievementsCarouselTests(TestCase):
    """The dashboard achievements carousel (apps.analytics.achievements)
    is shared across every user, not scoped to whoever's viewing it —
    show_achievements is a privacy setting ("don't show my stats to
    anyone"), not a personal "hide the carousel from me" toggle."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.User = get_user_model()
        self.alice = self.User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def _log_a_completed_workout(self, user=None):
        from decimal import Decimal

        from apps.exercises.models import Exercise
        from apps.workouts import services as workout_services

        exercise = Exercise.objects.create(name="Test Deadlift", owner=None)
        session = workout_services.start_session(user or self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, exercise)
        workout_services.log_set(performed, weight=Decimal("100"), reps=5)
        workout_services.complete_session(session)

    def test_no_carousel_with_no_completed_workouts(self):
        response = self.client.get(reverse("dashboard"))
        self.assertFalse(response.context["achievements"])
        self.assertNotContains(response, "achievements-carousel")

    def test_carousel_shows_once_a_workout_is_completed(self):
        self._log_a_completed_workout()
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "achievements-carousel")
        self.assertTrue(response.context["achievements"])

    def test_carousel_still_shows_a_housemates_achievements_when_this_users_toggle_is_off(self):
        """Regression: an earlier version treated show_achievements as
        "hide the carousel from me" — turning it off must only remove
        *this* user's own stats from the shared carousel, not the
        carousel itself, and a housemate's achievements must still
        appear."""
        bob = self.User.objects.create_user(username="bob", password="s3cret-pass")
        self._log_a_completed_workout(user=bob)
        self.alice.show_achievements = False
        self.alice.save()
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "achievements-carousel")
        display_names = {h.display_name for h in response.context["achievements"]}
        self.assertEqual(display_names, {"bob"})

    def test_opting_out_removes_this_users_own_achievements_from_the_carousel(self):
        self._log_a_completed_workout()
        self.alice.show_achievements = False
        self.alice.save()
        response = self.client.get(reverse("dashboard"))
        self.assertFalse(response.context["achievements"])
        self.assertNotContains(response, "achievements-carousel")


class RecentlyActiveListTests(TestCase):
    """The dashboard's "Recently active" list — same shared-across-
    users, privacy-toggle-gated pattern as the achievements carousel
    (both driven by apps.analytics.achievements)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.User = get_user_model()
        self.alice = self.User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_no_list_with_no_sessions_at_all(self):
        response = self.client.get(reverse("dashboard"))
        self.assertFalse(response.context["recently_active"])
        self.assertNotContains(response, "recent-activity-list")

    def test_list_shows_once_a_session_exists(self):
        from apps.workouts import services as workout_services

        workout_services.start_session(self.alice, workout=None)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "recent-activity-list")
        self.assertContains(response, "alice")

    def test_an_in_progress_session_shows_training_now(self):
        from apps.workouts import services as workout_services

        workout_services.start_session(self.alice, workout=None)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Training now")

    def test_opting_out_removes_this_user_from_the_list(self):
        from apps.workouts import services as workout_services

        workout_services.start_session(self.alice, workout=None)
        self.alice.show_achievements = False
        self.alice.save()
        response = self.client.get(reverse("dashboard"))
        self.assertFalse(response.context["recently_active"])
        self.assertNotContains(response, "recent-activity-list")
