from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import services, units
from .models import Activity, ActivityType

User = get_user_model()


class ActivityTypeSeedTests(TestCase):
    def test_seed_migration_creates_a_starting_set(self):
        names = set(ActivityType.objects.filter(owner=None).values_list("name", flat=True))
        self.assertEqual(
            names,
            {"Running", "Walking", "Cycling", "Swimming", "Hiking", "Rowing", "Yoga", "Other"},
        )


class DistanceUnitConversionTests(TestCase):
    def test_metric_converts_meters_to_km(self):
        km = units.distance_to_display(Decimal("5000"), "metric")
        self.assertEqual(km, Decimal("5.00"))
        self.assertEqual(units.distance_to_canonical(km, "metric"), Decimal("5000.00"))

    def test_imperial_converts_meters_to_miles(self):
        miles = units.distance_to_display(Decimal("1609.344"), "imperial")
        self.assertEqual(miles, Decimal("1.00"))

    def test_none_distance_stays_none(self):
        self.assertIsNone(units.distance_to_display(None, "metric"))
        self.assertIsNone(units.distance_to_canonical(None, "metric"))

    def test_unit_labels(self):
        self.assertEqual(units.distance_unit_label("metric"), "km")
        self.assertEqual(units.distance_unit_label("imperial"), "mi")


class ActivityTypeModelTests(TestCase):
    def test_two_users_can_each_have_a_custom_type_with_the_same_name(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        ActivityType.objects.create(name="Climbing", owner=alice)
        ActivityType.objects.create(name="Climbing", owner=bob)

    def test_is_custom_reflects_ownership(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        custom = ActivityType.objects.create(name="Climbing", owner=alice)
        system = ActivityType.objects.get(name="Running")
        self.assertTrue(custom.is_custom)
        self.assertFalse(system.is_custom)


class SummarizeServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.activity_type = ActivityType.objects.get(name="Running")

    def test_summarize_empty_history(self):
        summary = services.summarize([])
        self.assertEqual(summary.count, 0)
        self.assertEqual(summary.total_duration, timedelta())
        self.assertIsNone(summary.total_distance)
        self.assertIsNone(summary.total_calories)

    def test_summarize_totals_duration_distance_and_calories(self):
        activities = [
            Activity(
                user=self.alice,
                activity_type=self.activity_type,
                date="2026-01-01",
                duration=timedelta(minutes=30),
                distance=Decimal("5000"),
                calories=300,
            ),
            Activity(
                user=self.alice,
                activity_type=self.activity_type,
                date="2026-01-02",
                duration=timedelta(minutes=45),
                distance=Decimal("7000"),
                calories=None,
            ),
        ]
        summary = services.summarize(activities)
        self.assertEqual(summary.count, 2)
        self.assertEqual(summary.total_duration, timedelta(minutes=75))
        self.assertEqual(summary.total_distance, Decimal("12000"))
        self.assertEqual(summary.total_calories, 300)

    def test_summarize_treats_missing_distance_as_none_not_zero(self):
        activities = [
            Activity(
                user=self.alice,
                activity_type=self.activity_type,
                date="2026-01-01",
                duration=timedelta(minutes=30),
            )
        ]
        summary = services.summarize(activities)
        self.assertIsNone(summary.total_distance)


class ActivityViewPermissionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.activity_type = ActivityType.objects.get(name="Running")
        self.client.login(username="alice", password="s3cret-pass")

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("activities:type-list"))
        self.assertEqual(response.status_code, 302)

    def test_cannot_view_another_users_custom_type(self):
        bobs_type = ActivityType.objects.create(name="Bob Only", owner=self.bob)
        response = self.client.get(reverse("activities:history", args=[bobs_type.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_view_another_users_activity_entry(self):
        entry = Activity.objects.create(
            user=self.bob,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=30),
        )
        response = self.client.get(reverse("activities:entry-edit", args=[entry.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_delete_another_users_activity_entry(self):
        entry = Activity.objects.create(
            user=self.bob,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=30),
        )
        response = self.client.post(reverse("activities:entry-delete", args=[entry.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Activity.objects.filter(pk=entry.pk).exists())

    def test_history_only_shows_the_logged_in_users_own_entries(self):
        Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=30),
        )
        Activity.objects.create(
            user=self.bob,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=999),
        )
        response = self.client.get(reverse("activities:history", args=[self.activity_type.pk]))
        self.assertEqual(len(response.context["history"]), 1)

    def test_cannot_deactivate_another_users_custom_type(self):
        bobs_type = ActivityType.objects.create(name="Bob Only", owner=self.bob)
        response = self.client.post(reverse("activities:type-deactivate", args=[bobs_type.pk]))
        self.assertEqual(response.status_code, 404)
        bobs_type.refresh_from_db()
        self.assertTrue(bobs_type.active)


class LoggingFlowTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.activity_type = ActivityType.objects.get(name="Running")
        self.client.login(username="alice", password="s3cret-pass")

    def test_date_and_start_time_use_native_pickers_not_plain_text(self):
        """Regression: date/start_time used to render as plain text
        inputs the user had to type by hand instead of a native
        calendar/clock picker."""
        response = self.client.get(reverse("activities:history", args=[self.activity_type.pk]))
        self.assertContains(response, 'type="date"')
        self.assertContains(response, 'type="time"')

    def test_editing_an_activity_pre_fills_the_date_and_time_pickers_correctly(self):
        """The picker's pre-filled value must be ISO format
        (YYYY-MM-DD / HH:MM) — Django's locale-dependent default
        formatting doesn't reliably match what type="date"/type="time"
        expect."""
        entry = Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-03-15",
            start_time="07:30",
            duration=timedelta(minutes=30),
        )
        response = self.client.get(reverse("activities:entry-edit", args=[entry.pk]))
        self.assertContains(response, 'value="2026-03-15"')
        self.assertContains(response, 'value="07:30"')

    def test_logging_an_activity_in_metric_converts_km_to_canonical_meters(self):
        response = self.client.post(
            reverse("activities:log", args=[self.activity_type.pk]),
            {
                "date": "2026-01-01",
                "duration_minutes": "30",
                "distance": "5",
                "calories": "300",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        entry = Activity.objects.get(user=self.alice)
        self.assertEqual(entry.duration, timedelta(minutes=30))
        self.assertEqual(entry.distance, Decimal("5000.00"))
        self.assertEqual(entry.calories, 300)

    def test_logging_without_distance_leaves_it_null(self):
        self.client.post(
            reverse("activities:log", args=[self.activity_type.pk]),
            {"date": "2026-01-01", "duration_minutes": "60", "distance": "", "notes": ""},
        )
        entry = Activity.objects.get(user=self.alice)
        self.assertIsNone(entry.distance)

    def test_logging_in_imperial_converts_miles_to_canonical_meters(self):
        self.alice.unit_system = "imperial"
        self.alice.save()
        self.client.post(
            reverse("activities:log", args=[self.activity_type.pk]),
            {"date": "2026-01-01", "duration_minutes": "30", "distance": "1", "notes": ""},
        )
        entry = Activity.objects.get(user=self.alice)
        self.assertEqual(entry.distance, Decimal("1609.34"))

    def test_editing_an_activity_updates_duration_and_distance(self):
        entry = Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=30),
            distance=Decimal("5000"),
        )
        response = self.client.post(
            reverse("activities:entry-edit", args=[entry.pk]),
            {"date": "2026-01-01", "duration_minutes": "40", "distance": "6", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.duration, timedelta(minutes=40))
        self.assertEqual(entry.distance, Decimal("6000.00"))

    def test_deleting_an_activity_removes_it(self):
        entry = Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=30),
        )
        self.client.post(reverse("activities:entry-delete", args=[entry.pk]))
        self.assertFalse(Activity.objects.filter(pk=entry.pk).exists())

    def test_history_page_shows_a_summary_and_chart_once_two_entries_exist(self):
        Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=30),
        )
        Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-01-02",
            duration=timedelta(minutes=45),
        )
        response = self.client.get(reverse("activities:history", args=[self.activity_type.pk]))
        self.assertEqual(response.context["summary"].count, 2)
        self.assertIsNotNone(response.context["chart"])
        self.assertContains(response, "<svg")

    def test_chart_has_a_visible_heading_naming_the_metric_not_just_the_type(self):
        """Regression: the chart's title used to be just the activity
        type name (e.g. "Running"), which doesn't say the chart is
        specifically about duration (as opposed to distance/calories) --
        and it only ever reached the screen-reader-only aria-label, never
        anything a sighted user could see."""
        Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=30),
        )
        Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-01-02",
            duration=timedelta(minutes=45),
        )
        response = self.client.get(reverse("activities:history", args=[self.activity_type.pk]))
        self.assertContains(response, "<h2>Duration trend</h2>")

    def test_summary_total_distance_is_converted_to_the_display_unit(self):
        # Regression: summarize() totals canonical meters; the view must
        # convert before the template renders it next to "km"/"mi".
        Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=30),
            distance=Decimal("5000"),
        )
        Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-01-02",
            duration=timedelta(minutes=30),
            distance=Decimal("6000"),
        )
        response = self.client.get(reverse("activities:history", args=[self.activity_type.pk]))
        self.assertEqual(response.context["summary"].total_distance, Decimal("11.00"))
        self.assertContains(response, "total distance 11.00 km")


class CustomActivityTypeFlowTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_creating_a_custom_type_sets_owner(self):
        response = self.client.post(
            reverse("activities:type-create"), {"name": "Climbing"}
        )
        activity_type = ActivityType.objects.get(name="Climbing")
        self.assertEqual(activity_type.owner, self.alice)
        self.assertRedirects(response, reverse("activities:history", args=[activity_type.pk]))

    def test_deactivating_own_type_hides_it_from_the_visible_list(self):
        activity_type = ActivityType.objects.create(name="Climbing", owner=self.alice)
        self.client.post(reverse("activities:type-deactivate", args=[activity_type.pk]))
        response = self.client.get(reverse("activities:type-list"))
        names = {at.name for at in response.context["activity_types"]}
        self.assertNotIn("Climbing", names)


class ActivityTypeListEntryCountTests(TestCase):
    """Regression coverage for the list page's Count annotation (Phase 11
    query review): must count only the logged-in user's own entries
    against a type, even though a system type is shared by every user."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.activity_type = ActivityType.objects.get(name="Running")
        self.client.login(username="alice", password="s3cret-pass")

    def test_entry_count_reflects_only_the_current_users_activities(self):
        Activity.objects.create(
            user=self.alice,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=30),
        )
        Activity.objects.create(
            user=self.bob,
            activity_type=self.activity_type,
            date="2026-01-01",
            duration=timedelta(minutes=30),
        )
        Activity.objects.create(
            user=self.bob,
            activity_type=self.activity_type,
            date="2026-01-02",
            duration=timedelta(minutes=30),
        )
        response = self.client.get(reverse("activities:type-list"))
        running = next(
            at for at in response.context["activity_types"] if at.pk == self.activity_type.pk
        )
        self.assertEqual(running.entry_count, 1)
