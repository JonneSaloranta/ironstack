from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.charts import build_chart_series
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


class HealthcheckTests(TestCase):
    def test_healthcheck_returns_200_without_auth(self):
        response = self.client.get(reverse("healthcheck"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "ok")


class DashboardAccessTests(TestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
