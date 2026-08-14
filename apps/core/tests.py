from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from django.views.defaults import permission_denied, server_error

from apps.core.bmi import BMI_CATEGORIES, calculate_bmi, category_for, category_rows
from apps.core.charts import build_bar_series, build_chart_series
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
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, '<link rel="manifest" href="/manifest.json">')
        self.assertContains(response, "serviceWorker.register")


class DashboardAccessTests(TestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


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

    def test_bmi_is_not_shown_without_a_height(self):
        from apps.measurements.models import BodyMeasurement, MeasurementType

        body_weight_type = MeasurementType.objects.get(name="Body weight", owner=None)
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=body_weight_type, value=Decimal("82.5")
        )
        response = self.client.get(reverse("dashboard"))
        self.assertNotIn("bmi", response.context)
        self.assertContains(response, "Add your height")

    def test_bmi_is_not_shown_without_a_logged_body_weight(self):
        self.alice.height = Decimal("1.80")
        self.alice.save()
        response = self.client.get(reverse("dashboard"))
        self.assertNotIn("bmi", response.context)

    def test_a_nudge_to_log_body_weight_shows_once_height_is_set(self):
        """Regression: once a height was set but no body weight had ever
        been logged, the dashboard silently showed nothing at all about
        BMI — no card, no explanation, not even the "add your height"
        nudge (since height already existed) — just a gap where the
        feature seemed to have disappeared."""
        self.alice.height = Decimal("1.80")
        self.alice.save()
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Log a body weight")

    def test_no_body_weight_nudge_once_a_body_weight_exists(self):
        from apps.measurements.models import BodyMeasurement, MeasurementType

        self.alice.height = Decimal("1.80")
        self.alice.save()
        body_weight_type = MeasurementType.objects.get(name="Body weight", owner=None)
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=body_weight_type, value=Decimal("82.5")
        )
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, "Log a body weight")

    def test_bmi_and_category_are_shown_once_height_and_weight_both_exist(self):
        from apps.measurements.models import BodyMeasurement, MeasurementType

        self.alice.height = Decimal("1.80")
        self.alice.save()
        body_weight_type = MeasurementType.objects.get(name="Body weight", owner=None)
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=body_weight_type, value=Decimal("82.5")
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["bmi"], Decimal("25.5"))
        self.assertEqual(response.context["bmi_category"].name, "Overweight")
        self.assertContains(response, "Overweight")

    def test_bmi_card_shows_the_equivalent_weight_range_per_category(self):
        """Regression: the category ranges table only ever showed bare
        BMI numbers ("18.5–25") with no indication of what that actually
        means in kg/lb for this specific user's height."""
        self.alice.height = Decimal("1.80")
        self.alice.save()
        from apps.measurements.models import BodyMeasurement, MeasurementType

        body_weight_type = MeasurementType.objects.get(name="Body weight", owner=None)
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=body_weight_type, value=Decimal("82.5")
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "59.9")
        self.assertContains(response, "81.0")

    def test_bmi_heading_explains_the_abbreviation(self):
        self.alice.height = Decimal("1.80")
        self.alice.save()
        from apps.measurements.models import BodyMeasurement, MeasurementType

        body_weight_type = MeasurementType.objects.get(name="Body weight", owner=None)
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=body_weight_type, value=Decimal("82.5")
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, '<abbr tabindex="0" title="Body Mass Index">BMI</abbr>')

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

    def test_bmi_is_hidden_when_the_user_has_turned_it_off(self):
        from apps.measurements.models import BodyMeasurement, MeasurementType

        self.alice.height = Decimal("1.80")
        self.alice.show_bmi = False
        self.alice.save()
        body_weight_type = MeasurementType.objects.get(name="Body weight", owner=None)
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=body_weight_type, value=Decimal("82.5")
        )
        response = self.client.get(reverse("dashboard"))
        self.assertNotIn("bmi", response.context)
        # The height nudge shouldn't appear either — the user has
        # opted out of the whole BMI feature, not just this instance.
        self.assertNotContains(response, "Add your height")

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
        usernames = {h.username for h in response.context["achievements"]}
        self.assertEqual(usernames, {"bob"})

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
