from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.units import (
    kg_to_lb,
    km_to_meters,
    lb_to_kg,
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
