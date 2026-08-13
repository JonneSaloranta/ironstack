from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.exercises.models import Exercise, MuscleGroup
from apps.records.models import PersonalRecord, PRType
from apps.workouts import services as workout_services

from . import dateranges, services

User = get_user_model()


def _log_completed_session(user, exercise, weight, reps_list, *, days_ago=0):
    session = workout_services.start_session(user, workout=None)
    session.started_at = timezone.now() - timedelta(days=days_ago)
    session.save(update_fields=["started_at"])
    performed = workout_services.add_performed_exercise(session, exercise)
    for reps in reps_list:
        workout_services.log_set(performed, weight=weight, reps=reps)
    workout_services.complete_session(session)
    return session


class DateRangeTests(TestCase):
    def test_preset_resolves_relative_to_today(self):
        today = timezone.localdate()
        result = dateranges.resolve("7d")
        self.assertEqual(result.end, today)
        self.assertEqual(result.start, today - timedelta(days=7))

    def test_all_time_has_no_start(self):
        result = dateranges.resolve("all")
        self.assertIsNone(result.start)

    def test_unknown_key_falls_back_to_all_time(self):
        result = dateranges.resolve("not-a-real-range")
        self.assertEqual(result.key, "all")

    def test_explicit_start_overrides_a_preset_key(self):
        custom_start = date(2026, 1, 1)
        result = dateranges.resolve("7d", start=custom_start)
        self.assertEqual(result.key, "custom")
        self.assertEqual(result.start, custom_start)


class TrainingSummaryTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Squat", owner=None)

    def test_summary_totals_sessions_and_volume(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5, 5, 5])
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5, 5])
        summary = services.training_summary(self.alice, dateranges.resolve("all"))
        self.assertEqual(summary.session_count, 2)
        self.assertEqual(summary.total_volume, Decimal("2500"))  # 5*500 + 2*500

    def test_sessions_outside_the_range_are_excluded(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=100)
        summary = services.training_summary(self.alice, dateranges.resolve("7d"))
        self.assertEqual(summary.session_count, 0)
        self.assertEqual(summary.total_volume, Decimal("0"))

    def test_warmup_sets_are_excluded_from_volume(self):
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        workout_services.log_set(performed, weight=Decimal("40"), reps=10, is_warmup=True)
        workout_services.log_set(performed, weight=Decimal("100"), reps=5)
        workout_services.complete_session(session)
        summary = services.training_summary(self.alice, dateranges.resolve("all"))
        self.assertEqual(summary.total_volume, Decimal("500"))

    def test_failed_sets_still_count_toward_training_volume(self):
        # Unlike PR eligibility, a failed set still represents real work.
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        workout_services.log_set(performed, weight=Decimal("100"), reps=3, is_failure=True)
        workout_services.complete_session(session)
        summary = services.training_summary(self.alice, dateranges.resolve("all"))
        self.assertEqual(summary.total_volume, Decimal("300"))

    def test_summary_is_scoped_per_user(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _log_completed_session(bob, self.exercise, Decimal("999"), [5])
        summary = services.training_summary(self.alice, dateranges.resolve("all"))
        self.assertEqual(summary.session_count, 0)


class WeeklyVolumeSeriesTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Bench", owner=None)

    def test_no_history_returns_no_series(self):
        self.assertIsNone(services.weekly_volume_series(self.alice, dateranges.resolve("all")))

    def test_sets_are_grouped_into_iso_weeks(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=0)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=1)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=14)
        series = services.weekly_volume_series(self.alice, dateranges.resolve("all"))
        # Two sessions land in the same week (grouped into one bar), one
        # in an earlier week -> two bars total.
        self.assertEqual(len(series.bars), 2)


class MuscleGroupVolumeSeriesTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.chest = MuscleGroup.objects.create(name="Test Chest")
        self.triceps = MuscleGroup.objects.create(name="Test Triceps")
        self.exercise = Exercise.objects.create(name="Test Press", owner=None)
        self.exercise.primary_muscle_groups.set([self.chest, self.triceps])

    def test_a_set_contributes_full_volume_to_every_primary_muscle_group(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        series = services.muscle_group_volume_series(self.alice, dateranges.resolve("all"))
        totals = {bar.label: bar.value for bar in series.bars}
        self.assertEqual(totals["Test Chest"], Decimal("500"))
        self.assertEqual(totals["Test Triceps"], Decimal("500"))

    def test_no_history_returns_no_series(self):
        result = services.muscle_group_volume_series(self.alice, dateranges.resolve("all"))
        self.assertIsNone(result)


class PrHistoryServiceTests(TestCase):
    def test_pr_history_is_scoped_to_user_and_range(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        exercise = Exercise.objects.create(name="Test Deadlift", owner=None)

        PersonalRecord.objects.create(
            user=alice,
            exercise=exercise,
            record_type=PRType.MAX_WEIGHT,
            value=Decimal("100"),
            weight=Decimal("100"),
            reps=1,
            achieved_at=timezone.now(),
        )
        PersonalRecord.objects.create(
            user=alice,
            exercise=exercise,
            record_type=PRType.MAX_WEIGHT,
            value=Decimal("50"),
            weight=Decimal("50"),
            reps=1,
            achieved_at=timezone.now() - timedelta(days=100),
        )
        PersonalRecord.objects.create(
            user=bob,
            exercise=exercise,
            record_type=PRType.MAX_WEIGHT,
            value=Decimal("999"),
            weight=Decimal("999"),
            reps=1,
            achieved_at=timezone.now(),
        )

        recent = list(services.pr_history(alice, dateranges.resolve("7d")))
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].value, Decimal("100"))


class ExerciseAnalyticsServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test OHP", owner=None)

    def test_exercise_summary_totals_sessions_and_volume(self):
        _log_completed_session(self.alice, self.exercise, Decimal("50"), [5, 5])
        summary = services.exercise_summary(self.alice, self.exercise, dateranges.resolve("all"))
        self.assertEqual(summary.session_count, 1)
        self.assertEqual(summary.total_volume, Decimal("500"))

    def test_one_rm_trend_needs_at_least_two_sessions(self):
        _log_completed_session(self.alice, self.exercise, Decimal("50"), [5])
        result = services.exercise_one_rm_trend(
            self.alice, self.exercise, dateranges.resolve("all")
        )
        self.assertIsNone(result)

    def test_one_rm_trend_takes_the_best_estimate_per_session(self):
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        workout_services.log_set(performed, weight=Decimal("50"), reps=5)
        workout_services.log_set(performed, weight=Decimal("55"), reps=3)  # better estimate
        workout_services.complete_session(session)
        _log_completed_session(self.alice, self.exercise, Decimal("60"), [5], days_ago=7)

        series = services.exercise_one_rm_trend(
            self.alice, self.exercise, dateranges.resolve("all")
        )
        self.assertEqual(len(series.points), 2)

    def test_one_rm_trend_excludes_warmup_and_failed_sets(self):
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        workout_services.log_set(performed, weight=Decimal("200"), reps=5, is_warmup=True)
        workout_services.log_set(performed, weight=Decimal("50"), reps=5)
        workout_services.complete_session(session)
        _log_completed_session(self.alice, self.exercise, Decimal("55"), [5], days_ago=7)

        series = services.exercise_one_rm_trend(
            self.alice, self.exercise, dateranges.resolve("all")
        )
        # The 200kg warmup must never appear as the higher estimate.
        self.assertTrue(all(point.value < Decimal("200") for point in series.points))


class AnalyticsDashboardViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_dashboard_renders_with_no_history(self):
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"].session_count, 0)

    def test_default_range_is_30_days(self):
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.context["date_range"].key, "30d")

    def test_range_param_is_respected(self):
        response = self.client.get(reverse("analytics:dashboard"), {"range": "1y"})
        self.assertEqual(response.context["date_range"].key, "1y")

    def test_dashboard_reflects_another_users_data_only_for_that_user(self):
        exercise = Exercise.objects.create(name="Test Row", owner=None)
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _log_completed_session(bob, exercise, Decimal("999"), [5])
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.context["summary"].session_count, 0)


class ExerciseAnalyticsViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Curl", owner=None)
        self.client.login(username="alice", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("analytics:exercise", args=[self.exercise.pk]))
        self.assertEqual(response.status_code, 302)

    def test_renders_for_a_visible_exercise(self):
        response = self.client.get(reverse("analytics:exercise", args=[self.exercise.pk]))
        self.assertEqual(response.status_code, 200)

    def test_404_for_another_users_private_exercise(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        bobs_exercise = Exercise.objects.create(name="Bob Only", owner=bob)
        response = self.client.get(reverse("analytics:exercise", args=[bobs_exercise.pk]))
        self.assertEqual(response.status_code, 404)
