from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import units
from .models import BodyMeasurement, MeasurementType, UnitKind

User = get_user_model()


class MeasurementTypeSeedTests(TestCase):
    def test_seed_migration_creates_the_documented_types(self):
        names = set(MeasurementType.objects.filter(owner=None).values_list("name", flat=True))
        self.assertEqual(
            names,
            {
                "Body weight",
                "Body fat %",
                "Waist",
                "Chest",
                "Arm",
                "Thigh",
                "Hip",
                "Neck",
            },
        )

    def test_weight_and_percentage_and_length_kinds_are_assigned_correctly(self):
        self.assertEqual(MeasurementType.objects.get(name="Body weight").unit_kind, UnitKind.WEIGHT)
        self.assertEqual(
            MeasurementType.objects.get(name="Body fat %").unit_kind, UnitKind.PERCENTAGE
        )
        self.assertEqual(MeasurementType.objects.get(name="Waist").unit_kind, UnitKind.LENGTH)


class UnitConversionDispatchTests(TestCase):
    def test_weight_converts_for_imperial_users(self):
        display = units.to_display(Decimal("100"), UnitKind.WEIGHT, "imperial")
        self.assertEqual(display, Decimal("220.46"))
        self.assertEqual(
            units.to_canonical(display, UnitKind.WEIGHT, "imperial"), Decimal("100.00")
        )

    def test_weight_is_unchanged_for_metric_users(self):
        self.assertEqual(
            units.to_display(Decimal("82.5"), UnitKind.WEIGHT, "metric"), Decimal("82.5")
        )

    def test_length_converts_to_cm_for_metric_users(self):
        canonical = units.to_canonical(Decimal("85.5"), UnitKind.LENGTH, "metric")
        self.assertEqual(canonical, Decimal("0.8550"))
        self.assertEqual(units.to_display(canonical, UnitKind.LENGTH, "metric"), Decimal("85.5"))

    def test_length_converts_to_inches_for_imperial_users(self):
        canonical = units.to_canonical(Decimal("33.5"), UnitKind.LENGTH, "imperial")
        self.assertEqual(units.to_display(canonical, UnitKind.LENGTH, "imperial"), Decimal("33.5"))

    def test_percentage_is_never_converted(self):
        self.assertEqual(
            units.to_display(Decimal("18.5"), UnitKind.PERCENTAGE, "imperial"), Decimal("18.5")
        )
        self.assertEqual(
            units.to_canonical(Decimal("18.5"), UnitKind.PERCENTAGE, "imperial"), Decimal("18.5")
        )

    def test_unit_labels(self):
        self.assertEqual(units.display_unit_label(UnitKind.WEIGHT, "metric"), "kg")
        self.assertEqual(units.display_unit_label(UnitKind.WEIGHT, "imperial"), "lb")
        self.assertEqual(units.display_unit_label(UnitKind.LENGTH, "metric"), "cm")
        self.assertEqual(units.display_unit_label(UnitKind.LENGTH, "imperial"), "in")
        self.assertEqual(units.display_unit_label(UnitKind.PERCENTAGE, "metric"), "%")


class MeasurementTypeModelTests(TestCase):
    def test_two_users_can_each_have_a_custom_type_with_the_same_name(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        MeasurementType.objects.create(name="Calf", owner=alice, unit_kind=UnitKind.LENGTH)
        MeasurementType.objects.create(name="Calf", owner=bob, unit_kind=UnitKind.LENGTH)

    def test_is_custom_reflects_ownership(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        custom = MeasurementType.objects.create(name="Calf", owner=alice, unit_kind=UnitKind.LENGTH)
        system = MeasurementType.objects.get(name="Waist")
        self.assertTrue(custom.is_custom)
        self.assertFalse(system.is_custom)


class MeasurementViewPermissionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.measurement_type = MeasurementType.objects.get(name="Body weight")
        self.client.login(username="alice", password="s3cret-pass")

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("measurements:type-list"))
        self.assertEqual(response.status_code, 302)

    def test_cannot_view_another_users_custom_type(self):
        bobs_type = MeasurementType.objects.create(
            name="Bob Only", owner=self.bob, unit_kind=UnitKind.LENGTH
        )
        response = self.client.get(reverse("measurements:history", args=[bobs_type.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_view_another_users_measurement_entry(self):
        entry = BodyMeasurement.objects.create(
            user=self.bob, measurement_type=self.measurement_type, value=Decimal("80")
        )
        response = self.client.get(reverse("measurements:entry-edit", args=[entry.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_delete_another_users_measurement_entry(self):
        entry = BodyMeasurement.objects.create(
            user=self.bob, measurement_type=self.measurement_type, value=Decimal("80")
        )
        response = self.client.post(reverse("measurements:entry-delete", args=[entry.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(BodyMeasurement.objects.filter(pk=entry.pk).exists())

    def test_history_only_shows_the_logged_in_users_own_entries(self):
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=self.measurement_type, value=Decimal("80")
        )
        BodyMeasurement.objects.create(
            user=self.bob, measurement_type=self.measurement_type, value=Decimal("999")
        )
        response = self.client.get(reverse("measurements:history", args=[self.measurement_type.pk]))
        self.assertEqual(len(response.context["history"]), 1)

    def test_cannot_deactivate_another_users_custom_type(self):
        bobs_type = MeasurementType.objects.create(
            name="Bob Only", owner=self.bob, unit_kind=UnitKind.LENGTH
        )
        response = self.client.post(reverse("measurements:type-deactivate", args=[bobs_type.pk]))
        self.assertEqual(response.status_code, 404)
        bobs_type.refresh_from_db()
        self.assertTrue(bobs_type.active)


class LoggingFlowTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.measurement_type = MeasurementType.objects.get(name="Body weight")
        self.client.login(username="alice", password="s3cret-pass")

    def test_logging_a_reading_in_metric_stores_the_canonical_kg_value(self):
        response = self.client.post(
            reverse("measurements:log", args=[self.measurement_type.pk]),
            {"value": "82.5", "recorded_at": "2026-01-01 08:00:00", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        entry = BodyMeasurement.objects.get(user=self.alice)
        self.assertEqual(entry.value, Decimal("82.5"))

    def test_logging_a_reading_in_imperial_converts_to_canonical_kg(self):
        self.alice.unit_system = "imperial"
        self.alice.save()
        self.client.post(
            reverse("measurements:log", args=[self.measurement_type.pk]),
            {"value": "220.46", "recorded_at": "2026-01-01 08:00:00", "notes": ""},
        )
        entry = BodyMeasurement.objects.get(user=self.alice)
        self.assertEqual(entry.value, Decimal("100.00"))

    def test_editing_a_reading_updates_the_canonical_value(self):
        entry = BodyMeasurement.objects.create(
            user=self.alice, measurement_type=self.measurement_type, value=Decimal("80")
        )
        response = self.client.post(
            reverse("measurements:entry-edit", args=[entry.pk]),
            {"value": "81.5", "recorded_at": "2026-01-01 08:00:00", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.value, Decimal("81.5"))

    def test_recorded_at_uses_a_native_datetime_picker_not_plain_text(self):
        """Regression: recorded_at used to render as a plain text input
        the user had to type by hand instead of a native picker."""
        response = self.client.get(
            reverse("measurements:history", args=[self.measurement_type.pk])
        )
        self.assertContains(response, 'type="datetime-local"')

    def test_editing_pre_fills_the_datetime_picker_in_iso_format(self):
        from datetime import datetime

        from django.utils import timezone as dj_timezone

        entry = BodyMeasurement.objects.create(
            user=self.alice,
            measurement_type=self.measurement_type,
            value=Decimal("80"),
            recorded_at=dj_timezone.make_aware(datetime(2026, 3, 15, 7, 30)),
        )
        response = self.client.get(reverse("measurements:entry-edit", args=[entry.pk]))
        self.assertContains(response, 'value="2026-03-15T07:30"')

    def test_a_datetime_local_style_submission_is_accepted(self):
        """The picker itself submits a "T"-separated value, not the
        space-separated one Django defaults to accepting."""
        response = self.client.post(
            reverse("measurements:log", args=[self.measurement_type.pk]),
            {"value": "80", "recorded_at": "2026-03-15T07:30", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        entry = BodyMeasurement.objects.get(user=self.alice)
        self.assertEqual(entry.recorded_at.strftime("%Y-%m-%d %H:%M"), "2026-03-15 07:30")

    def test_deleting_a_reading_removes_it(self):
        entry = BodyMeasurement.objects.create(
            user=self.alice, measurement_type=self.measurement_type, value=Decimal("80")
        )
        self.client.post(reverse("measurements:entry-delete", args=[entry.pk]))
        self.assertFalse(BodyMeasurement.objects.filter(pk=entry.pk).exists())

    def test_history_page_renders_a_chart_once_two_readings_exist(self):
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=self.measurement_type, value=Decimal("80")
        )
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=self.measurement_type, value=Decimal("81")
        )
        response = self.client.get(reverse("measurements:history", args=[self.measurement_type.pk]))
        self.assertIsNotNone(response.context["chart"])
        self.assertContains(response, "<svg")

    def test_chart_has_a_visible_heading_not_just_a_screen_reader_label(self):
        """Regression: the chart used to carry its title only in the SVG's
        aria-label, invisible to sighted users."""
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=self.measurement_type, value=Decimal("80")
        )
        BodyMeasurement.objects.create(
            user=self.alice, measurement_type=self.measurement_type, value=Decimal("81")
        )
        response = self.client.get(reverse("measurements:history", args=[self.measurement_type.pk]))
        self.assertContains(response, "<h2>Trend</h2>")


class CustomMeasurementTypeFlowTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_creating_a_custom_type_sets_owner(self):
        response = self.client.post(
            reverse("measurements:type-create"),
            {"name": "Calf", "unit_kind": UnitKind.LENGTH},
        )
        measurement_type = MeasurementType.objects.get(name="Calf")
        self.assertEqual(measurement_type.owner, self.alice)
        self.assertRedirects(response, reverse("measurements:history", args=[measurement_type.pk]))

    def test_deactivating_own_type_hides_it_from_the_visible_list(self):
        measurement_type = MeasurementType.objects.create(
            name="Calf", owner=self.alice, unit_kind=UnitKind.LENGTH
        )
        self.client.post(reverse("measurements:type-deactivate", args=[measurement_type.pk]))
        response = self.client.get(reverse("measurements:type-list"))
        names = {mt.name for mt in response.context["measurement_types"]}
        self.assertNotIn("Calf", names)


class MeasurementTypeContentTranslationTests(TestCase):
    """Seeded measurement type *names* are content, not UI chrome — the
    stored value stays canonical English (matched by get_or_create
    elsewhere), but the display goes through gettext too, via
    apps.measurements.i18n_content's extraction catalog — see
    docs/ARCHITECTURE.md "Internationalization"."""

    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", password="s3cret-pass", language="fi"
        )
        self.client.login(username="alice", password="s3cret-pass")

    def test_measurement_type_name_renders_translated_for_a_non_english_user(self):
        measurement_type = MeasurementType.objects.get(name="Waist", owner=None)
        response = self.client.get(reverse("measurements:history", args=[measurement_type.pk]))
        self.assertContains(response, "Vyötärö")
        self.assertNotContains(response, ">Waist<")

    def test_a_content_name_containing_a_percent_sign_still_translates(self):
        """Regression: Django's `{% trans someobj.name %}` tag doubles every
        "%" in a resolved *variable* before the gettext lookup and undoes
        it after (meant for literal `%%` written in template source, but
        applied to variables too) — so "Body fat %" looked up "Body fat %%",
        found nothing, and silently fell back to English. Fixed by using
        apps.core.templatetags.core_extras.translate_content (a direct
        gettext() call) instead of `{% trans %}` for this content."""
        measurement_type = MeasurementType.objects.get(name="Body fat %", owner=None)
        response = self.client.get(reverse("measurements:history", args=[measurement_type.pk]))
        self.assertContains(response, "Rasvaprosentti")
        self.assertNotContains(response, "Body fat %")

    def test_a_users_own_custom_type_name_is_never_translated(self):
        """gettext only ever matches strings actually present in the
        catalog — a custom name a user typed themselves was never
        extracted into it, so it always renders exactly as typed,
        regardless of UI language."""
        measurement_type = MeasurementType.objects.create(
            name="My Weird Custom Measurement", owner=self.alice, unit_kind=UnitKind.LENGTH
        )
        response = self.client.get(reverse("measurements:history", args=[measurement_type.pk]))
        self.assertContains(response, "My Weird Custom Measurement")
