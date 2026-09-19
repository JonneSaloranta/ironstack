import re
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.formats import number_format

from apps.exercises.models import Exercise, MuscleGroup
from apps.records import services as records_services
from apps.records.models import PersonalRecord, PRType
from apps.workouts import services as workout_services

from . import achievements, dateranges, services

User = get_user_model()


def _log_completed_session(user, exercise, weight, reps_list, *, days_ago=0):
    session = workout_services.start_session(user, workout=None)
    session.started_at = timezone.now() - timedelta(days=days_ago)
    session.save(update_fields=["started_at"])
    performed = workout_services.add_performed_exercise(session, exercise)
    for reps in reps_list:
        workout_services.log_set(performed, weight=weight, reps=reps)
    workout_services.complete_session(session)
    if days_ago:
        # complete_session() always stamps ended_at with the real "now"
        # — backdate it too so a session "logged N days ago" actually
        # looks like one throughout, not just at its own started_at.
        # Callers that need started_at/ended_at to diverge (e.g. a
        # session that ran long) set ended_at explicitly afterward
        # instead of using this shortcut.
        session.ended_at = timezone.now() - timedelta(days=days_ago)
        session.save(update_fields=["ended_at"])
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

    def test_total_volume_converts_to_pounds_for_an_imperial_user(self):
        """Regression: total_volume used to always be raw kg, so an
        imperial-preference user saw a "kg" figure mislabeled/rendered as
        if it were their preferred unit."""
        self.alice.unit_system = "imperial"
        self.alice.save()
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])  # 500 kg volume
        summary = services.training_summary(self.alice, dateranges.resolve("all"))
        self.assertEqual(summary.total_volume, Decimal("1102.31"))  # 500 kg -> lb


class WeeklyVolumeSeriesTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Bench", owner=None)

    def test_no_history_returns_no_series(self):
        self.assertIsNone(services.weekly_volume_series(self.alice, dateranges.resolve("all")))

    def test_sets_are_grouped_into_iso_weeks(self):
        # Regression: days_ago=1 for the second session used to be a
        # fixed offset, which put it in an *earlier* ISO week than
        # days_ago=0 whenever "today" happened to be a Monday (ISO
        # weeks start on Monday, so "yesterday" is already last week
        # on that one day out of seven) — producing 3 bars instead of
        # the 2 this test expects. Capped at today.weekday() (0 on a
        # Monday) so the two sessions are always provably in the same
        # ISO week regardless of which real-world day the test runs on.
        same_week_gap = min(1, date.today().weekday())
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=0)
        _log_completed_session(
            self.alice, self.exercise, Decimal("100"), [5], days_ago=same_week_gap
        )
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=14)
        series = services.weekly_volume_series(self.alice, dateranges.resolve("all"))
        # Two sessions land in the same week (grouped into one bar), one
        # in an earlier week -> two bars total.
        self.assertEqual(len(series.bars), 2)

    def test_bar_values_convert_to_pounds_for_an_imperial_user(self):
        self.alice.unit_system = "imperial"
        self.alice.save()
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=0)
        series = services.weekly_volume_series(self.alice, dateranges.resolve("all"))
        self.assertEqual(series.bars[0].value, Decimal("1102.31"))


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


class PrHistoryGroupedByExerciseServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.squat = Exercise.objects.create(name="Test Squat", owner=None)
        self.bench = Exercise.objects.create(name="Test Bench", owner=None)

    def _pr(self, exercise, record_type, value, achieved_at):
        return PersonalRecord.objects.create(
            user=self.alice,
            exercise=exercise,
            record_type=record_type,
            value=value,
            weight=value,
            reps=1,
            achieved_at=achieved_at,
        )

    def test_several_record_types_for_one_exercise_become_one_group(self):
        now = timezone.now()
        self._pr(self.squat, PRType.MAX_WEIGHT, Decimal("100"), now)
        self._pr(self.squat, PRType.ESTIMATED_1RM, Decimal("110"), now - timedelta(minutes=1))
        self._pr(self.squat, PRType.SET_VOLUME, Decimal("500"), now - timedelta(minutes=2))

        groups = services.pr_history_grouped_by_exercise(self.alice, dateranges.resolve("7d"))

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].exercise, self.squat)
        # All three are "headline" types (max weight/1RM/volume) — none
        # relegated to secondary.
        self.assertEqual(len(groups[0].primary_records), 3)
        self.assertEqual(groups[0].secondary_records, [])

    def test_rep_specific_and_rep_prs_are_secondary(self):
        now = timezone.now()
        self._pr(self.squat, PRType.MAX_WEIGHT, Decimal("100"), now)
        self._pr(self.squat, PRType.REP_SPECIFIC_PR, Decimal("90"), now - timedelta(minutes=1))
        self._pr(self.squat, PRType.REP_PR, Decimal("12"), now - timedelta(minutes=2))

        groups = services.pr_history_grouped_by_exercise(self.alice, dateranges.resolve("7d"))

        self.assertEqual(len(groups[0].primary_records), 1)
        self.assertEqual(groups[0].primary_records[0].record_type, PRType.MAX_WEIGHT)
        self.assertEqual(
            {r.record_type for r in groups[0].secondary_records},
            {PRType.REP_SPECIFIC_PR, PRType.REP_PR},
        )

    def test_an_exercise_with_only_secondary_types_falls_back_to_showing_them_directly(self):
        # No max weight/1RM/volume hit in range for this exercise — a
        # card with nothing but a name and a chevron would look broken,
        # so its one secondary-type record is promoted to primary.
        now = timezone.now()
        self._pr(self.squat, PRType.REP_SPECIFIC_PR, Decimal("90"), now)

        groups = services.pr_history_grouped_by_exercise(self.alice, dateranges.resolve("7d"))

        self.assertEqual(len(groups[0].primary_records), 1)
        self.assertEqual(groups[0].primary_records[0].record_type, PRType.REP_SPECIFIC_PR)
        self.assertEqual(groups[0].secondary_records, [])

    def test_groups_are_ordered_by_each_exercises_own_most_recent_record(self):
        now = timezone.now()
        # Bench's most recent PR is newer than squat's, even though
        # squat has an even older second record — group order should
        # follow the newest record per exercise, not creation order.
        self._pr(self.squat, PRType.MAX_WEIGHT, Decimal("100"), now - timedelta(hours=2))
        self._pr(self.bench, PRType.MAX_WEIGHT, Decimal("80"), now - timedelta(hours=1))
        self._pr(self.squat, PRType.SET_VOLUME, Decimal("500"), now - timedelta(hours=3))

        groups = services.pr_history_grouped_by_exercise(self.alice, dateranges.resolve("7d"))

        self.assertEqual([g.exercise for g in groups], [self.bench, self.squat])

    def test_limit_applies_to_underlying_records_not_to_groups(self):
        now = timezone.now()
        self._pr(self.squat, PRType.MAX_WEIGHT, Decimal("100"), now)
        self._pr(self.bench, PRType.MAX_WEIGHT, Decimal("80"), now - timedelta(hours=1))

        # Only the single most recent record is fetched — bench's PR
        # falls outside that and its group never appears at all.
        groups = services.pr_history_grouped_by_exercise(
            self.alice, dateranges.resolve("7d"), limit=1
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].exercise, self.squat)

    def test_no_records_returns_no_groups(self):
        groups = services.pr_history_grouped_by_exercise(self.alice, dateranges.resolve("7d"))
        self.assertEqual(groups, [])


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


class AchievementsTests(TestCase):
    """apps.analytics.achievements — all-time dashboard-carousel
    highlights, deliberately unbounded by any DateRange (unlike
    everything else in this file) and, unlike everything else in this
    file, shared across every user rather than scoped to one."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Squat", owner=None)

    def test_no_completed_workouts_returns_no_highlights(self):
        self.assertEqual(achievements.achievement_highlights(), [])

    def test_longest_streak_counts_consecutive_days(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=2)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=1)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=0)
        self.assertEqual(achievements.longest_workout_streak_days(self.alice), 3)

    def test_a_gap_breaks_the_streak(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=5)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=1)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=0)
        self.assertEqual(achievements.longest_workout_streak_days(self.alice), 2)

    def test_the_longest_streak_survives_after_it_ends(self):
        """"Longest streak" is an all-time best, not the user's current
        streak — a 3-day run from ten days ago should still show even
        though today's run is only 1 day so far."""
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=10)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=9)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=8)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=0)
        self.assertEqual(achievements.longest_workout_streak_days(self.alice), 3)

    def test_two_sessions_on_the_same_day_count_as_one_day(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=0)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=0)
        self.assertEqual(achievements.longest_workout_streak_days(self.alice), 1)

    def test_highlights_always_include_streak_and_workout_count(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        icons = [h.icon for h in achievements.achievement_highlights()]
        self.assertIn("streak", icons)
        self.assertIn("workouts", icons)

    def test_each_highlight_carries_its_own_display_name(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        highlights = achievements.achievement_highlights()
        self.assertTrue(all(h.display_name == "alice" for h in highlights))

    def test_each_highlight_also_carries_the_real_username(self):
        """Distinct from display_name (which can carry a first name
        too) — apps.analytics.views.MemberProfileView's own link needs
        the real, stable username to build a URL from."""
        self.alice.first_name = "Alice"
        self.alice.show_name_to_others = True
        self.alice.save()
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        highlights = achievements.achievement_highlights()
        self.assertTrue(all(h.display_name == "alice (Alice)" for h in highlights))
        self.assertTrue(all(h.username == "alice" for h in highlights))

    def test_workout_count_reflects_only_completed_sessions(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        workout_services.start_session(self.alice, workout=None)  # left in progress
        highlights = achievements.achievement_highlights()
        workouts = next(h for h in highlights if h.icon == "workouts")
        self.assertIn("1", workouts.value)

    def test_pr_highlight_only_appears_once_a_pr_exists(self):
        session = workout_services.start_session(self.alice, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        logged_set = workout_services.log_set(performed, weight=Decimal("100"), reps=5)
        records_services.check_and_record_prs(logged_set)
        workout_services.complete_session(session)
        icons = [h.icon for h in achievements.achievement_highlights()]
        self.assertIn("pr", icons)

    def test_no_pr_highlight_without_any_recorded_prs(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        icons = [h.icon for h in achievements.achievement_highlights()]
        self.assertNotIn("pr", icons)

    def test_volume_highlight_reflects_the_users_display_unit(self):
        self.alice.unit_system = "imperial"
        self.alice.save()
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])  # 500 kg
        highlights = achievements.achievement_highlights()
        volume = next(h for h in highlights if h.icon == "volume")
        self.assertIn("lb", volume.value)

    def test_no_volume_highlight_without_any_logged_sets(self):
        session = workout_services.start_session(self.alice, workout=None)
        workout_services.complete_session(session)  # completed, but no exercises/sets at all
        icons = [h.icon for h in achievements.achievement_highlights()]
        self.assertNotIn("volume", icons)

    def test_highlights_include_every_opted_in_user(self):
        """The carousel is shared, not personal — regression: an earlier
        version scoped this to a single viewing user."""
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        _log_completed_session(bob, self.exercise, Decimal("999"), [5])
        display_names = {h.display_name for h in achievements.achievement_highlights()}
        self.assertEqual(display_names, {"alice", "bob"})

    def test_a_user_who_opted_out_is_excluded_entirely(self):
        """show_achievements is a privacy setting ("don't show my stats
        to anyone"), not a personal display toggle — turning it off
        removes that user's own highlights from the shared result the
        same way it would for anyone else viewing it."""
        self.alice.show_achievements = False
        self.alice.save()
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        self.assertEqual(achievements.achievement_highlights(), [])

    def test_one_users_data_does_not_leak_into_anothers_figures(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        _log_completed_session(bob, self.exercise, Decimal("999"), [5, 5])
        highlights = achievements.achievement_highlights()
        alice_workouts = next(
            h for h in highlights if h.icon == "workouts" and h.display_name == "alice"
        )
        bob_workouts = next(
            h for h in highlights if h.icon == "workouts" and h.display_name == "bob"
        )
        self.assertIn("1", alice_workouts.value)
        self.assertIn("1", bob_workouts.value)

    def test_display_name_includes_the_first_name_when_opted_in(self):
        self.alice.first_name = "Alice"
        self.alice.save()
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        highlights = achievements.achievement_highlights()
        self.assertTrue(all(h.display_name == "alice (Alice)" for h in highlights))

    def test_display_name_is_just_the_username_when_opted_out(self):
        self.alice.first_name = "Alice"
        self.alice.show_name_to_others = False
        self.alice.save()
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        highlights = achievements.achievement_highlights()
        self.assertTrue(all(h.display_name == "alice" for h in highlights))


class RecentlyActiveUsersTests(TestCase):
    """apps.analytics.achievements.recently_active_users — the
    dashboard's "Recently active" list."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Squat", owner=None)

    def test_a_user_with_no_sessions_at_all_is_excluded(self):
        self.assertEqual(achievements.recently_active_users(), [])

    def test_starting_a_session_counts_as_activity_even_incomplete(self):
        """A session doesn't have to be *completed* to count — starting
        one is itself a sign of activity, unlike the achievements
        carousel's per-figure counts."""
        workout_services.start_session(self.alice, workout=None)
        display_names = [entry.display_name for entry in achievements.recently_active_users()]
        self.assertEqual(display_names, ["alice"])

    def test_each_entry_also_carries_the_real_username(self):
        workout_services.start_session(self.alice, workout=None)
        usernames = [entry.username for entry in achievements.recently_active_users()]
        self.assertEqual(usernames, ["alice"])

    def test_most_recently_active_user_comes_first(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=5)
        _log_completed_session(bob, self.exercise, Decimal("100"), [5])  # just now

        display_names = [entry.display_name for entry in achievements.recently_active_users()]
        self.assertEqual(display_names, ["bob", "alice"])

    def test_only_the_latest_session_counts_per_user(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=10)
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=0)
        entries = achievements.recently_active_users()
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].is_recent)

    def test_an_in_progress_session_is_flagged_as_training_now(self):
        workout_services.start_session(self.alice, workout=None)
        entry = achievements.recently_active_users()[0]
        self.assertTrue(entry.is_in_progress)

    def test_a_completed_session_is_not_flagged_as_training_now(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        entry = achievements.recently_active_users()[0]
        self.assertFalse(entry.is_in_progress)

    def test_activity_older_than_a_day_is_not_flagged_recent(self):
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5], days_ago=2)
        entry = achievements.recently_active_users()[0]
        self.assertFalse(entry.is_recent)

    def test_last_active_at_is_when_the_session_ended_not_when_it_started(self):
        """Regression: a session that ran long — started days ago,
        finished just now — used to show as "days ago" on the
        dashboard, keyed off started_at. It should read as barely any
        time ago at all, since that's when this user actually stopped
        training."""
        session = _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        session.started_at = timezone.now() - timedelta(days=3)
        session.save(update_fields=["started_at"])  # ended_at stays "just now"

        entry = achievements.recently_active_users()[0]
        self.assertEqual(entry.last_active_at, session.ended_at)
        self.assertTrue(entry.is_recent)

    def test_a_user_who_opted_out_is_excluded(self):
        self.alice.show_achievements = False
        self.alice.save()
        _log_completed_session(self.alice, self.exercise, Decimal("100"), [5])
        self.assertEqual(achievements.recently_active_users(), [])

    def test_the_list_is_capped_at_the_given_limit(self):
        for i in range(3):
            user = User.objects.create_user(username=f"lifter{i}", password="s3cret-pass")
            _log_completed_session(user, self.exercise, Decimal("100"), [5])
        self.assertEqual(len(achievements.recently_active_users(limit=2)), 2)


class MemberProfileViewTests(TestCase):
    """apps.analytics.views.MemberProfileView — another user's public
    fitness profile, asked for directly. Gated by the same
    User.show_achievements opt-out the achievements carousel/"Recently
    active" list already use, not a second, separate setting."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Squat", owner=None)
        self.client.login(username="alice", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("analytics:member-profile", args=[self.bob.username]))
        self.assertEqual(response.status_code, 302)

    def test_shows_an_opted_in_users_profile(self):
        response = self.client.get(reverse("analytics:member-profile", args=[self.bob.username]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "bob")

    def test_an_opted_out_users_profile_404s_for_someone_else(self):
        self.bob.show_achievements = False
        self.bob.save()
        response = self.client.get(reverse("analytics:member-profile", args=[self.bob.username]))
        self.assertEqual(response.status_code, 404)

    def test_a_user_can_always_view_their_own_profile_even_opted_out(self):
        self.alice.show_achievements = False
        self.alice.save()
        response = self.client.get(
            reverse("analytics:member-profile", args=[self.alice.username])
        )
        self.assertEqual(response.status_code, 200)

    def test_a_nonexistent_username_404s(self):
        response = self.client.get(reverse("analytics:member-profile", args=["ghost"]))
        self.assertEqual(response.status_code, 404)

    def test_shows_member_since_date(self):
        response = self.client.get(reverse("analytics:member-profile", args=[self.bob.username]))
        self.assertContains(response, self.bob.date_joined.date().isoformat())

    def test_shows_highlights_for_a_user_with_completed_workouts(self):
        _log_completed_session(self.bob, self.exercise, Decimal("100"), [5])
        response = self.client.get(reverse("analytics:member-profile", args=[self.bob.username]))
        self.assertContains(response, "Longest streak")

    def test_shows_an_empty_state_for_a_user_with_no_workouts(self):
        response = self.client.get(reverse("analytics:member-profile", args=[self.bob.username]))
        self.assertContains(response, "No completed workouts yet.")

    def test_shows_recent_prs(self):
        session = workout_services.start_session(self.bob, workout=None)
        performed = workout_services.add_performed_exercise(session, self.exercise)
        logged_set = workout_services.log_set(performed, weight=Decimal("100"), reps=5)
        records_services.check_and_record_prs(logged_set)
        workout_services.complete_session(session)
        response = self.client.get(reverse("analytics:member-profile", args=[self.bob.username]))
        self.assertContains(response, "Test Squat")

    def test_shows_no_nutrition_or_body_weight_data(self):
        """Asked for directly: only workout/PR/streak figures, nothing
        about food, calories, or logged body weight. Scoped to this
        page's own content, past its <h1> — base.html's shared chrome
        mentions "nutrition" twice already, on every single
        authenticated page regardless of this one's own content: once
        in the site-wide SEO <meta name="description">, and once for
        the bottom-nav's own "Nutrition" tab."""
        response = self.client.get(reverse("analytics:member-profile", args=[self.bob.username]))
        content = response.content.decode().lower()
        content_block = content.split(">bob</h1>", 1)[-1]
        for forbidden in ("calorie", "nutrition", "body weight", "kcal"):
            self.assertNotIn(forbidden, content_block)

    def test_achievements_carousel_links_to_the_member_profile(self):
        _log_completed_session(self.bob, self.exercise, Decimal("100"), [5])
        response = self.client.get(reverse("dashboard"))
        self.assertContains(
            response, reverse("analytics:member-profile", args=[self.bob.username])
        )

    def test_recently_active_list_links_to_the_member_profile(self):
        workout_services.start_session(self.bob, workout=None)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(
            response, reverse("analytics:member-profile", args=[self.bob.username])
        )


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

    def test_bar_charts_render_a_table_with_readable_category_labels(self):
        """Regression: the bar charts used to have no visible way at all
        to tell which bar was which category (no x-axis text, no legend,
        no table) -- only a hover tooltip, undiscoverable on touch
        devices. The category name must appear as real page text, not
        just inside an SVG <title> tooltip."""
        chest = MuscleGroup.objects.create(name="Test Chest Bar")
        exercise = Exercise.objects.create(name="Test Bench Bar", owner=None)
        exercise.primary_muscle_groups.set([chest])
        _log_completed_session(self.alice, exercise, Decimal("80"), [5, 5, 5])

        response = self.client.get(reverse("analytics:dashboard"))
        self.assertContains(response, '<table class="bar-chart-table">')
        self.assertContains(response, "<th scope=\"row\">Test Chest Bar</th>")

    def test_bar_chart_labels_the_bars_directly_not_just_the_table(self):
        """Regression: bars were deliberately unlabeled in the SVG itself
        (only the table below named them), which read as broken/blank —
        a chart of same-colored, nameless bars. Each bar now carries its
        own <text> label too."""
        chest = MuscleGroup.objects.create(name="Test Chest Direct")
        exercise = Exercise.objects.create(name="Test Bench Direct", owner=None)
        exercise.primary_muscle_groups.set([chest])
        _log_completed_session(self.alice, exercise, Decimal("80"), [5, 5, 5])

        response = self.client.get(reverse("analytics:dashboard"))
        self.assertContains(response, 'class="bar-label"')
        self.assertContains(response, ">Test Chest Direct<")

    def test_bar_geometry_stays_period_separated_under_a_comma_decimal_locale(self):
        """Regression: apps.core.charts.build_bar_series' x/y/width/height
        are Decimals, and Django auto-localizes {{ }} output per the
        viewing user's own language (User.language) — under "fi" that's
        a comma decimal separator ("148,00"), which isn't valid SVG
        numeric syntax. Browsers silently fail to parse a comma-broken
        coordinate, so every bar rendered at zero size — invisible, not
        just misformatted — for any user with a comma-decimal UI
        language. templates/core/_bar_chart.html wraps the SVG in
        {% localize off %} to keep these internal plot coordinates
        locale-independent; bar.value (shown to the user as text, not a
        coordinate) still renders with the comma this user would expect.
        """
        self.alice.language = "fi"
        self.alice.save(update_fields=["language"])
        chest = MuscleGroup.objects.create(name="Test Chest Locale")
        exercise = Exercise.objects.create(name="Test Bench Locale", owner=None)
        exercise.primary_muscle_groups.set([chest])
        _log_completed_session(self.alice, exercise, Decimal("80"), [5, 5, 5])

        response = self.client.get(reverse("analytics:dashboard"))
        content = response.content.decode()
        for match in re.finditer(r'<rect class="bar-rect"([^>]+)>', content):
            attrs = match.group(1)
            for name in ("x", "y", "width", "height"):
                value = re.search(rf'{name}="([^"]+)"', attrs).group(1)
                self.assertNotIn(",", value, f"{name}={value!r} is not valid SVG syntax")
        # The tooltip text itself (not a coordinate) should still render
        # with the comma decimal separator "fi" expects.
        bar_value = response.context["muscle_group_chart"].bars[0].value
        with translation.override("fi"):
            self.assertIn(f"{number_format(bar_value)} kg", content)

    def test_recent_prs_renders_one_box_per_exercise_not_per_record_type(self):
        """Regression: several record_types hit for the same exercise
        used to render as several near-identical cards — one per
        PersonalRecord row — instead of one grouped box per exercise."""
        exercise = Exercise.objects.create(name="Test Deadlift Group", owner=None)
        now = timezone.now()
        for record_type, value in [
            (PRType.MAX_WEIGHT, Decimal("100")),
            (PRType.ESTIMATED_1RM, Decimal("110")),
            (PRType.SET_VOLUME, Decimal("500")),
        ]:
            PersonalRecord.objects.create(
                user=self.alice,
                exercise=exercise,
                record_type=record_type,
                value=value,
                weight=value,
                reps=1,
                achieved_at=now,
            )

        response = self.client.get(reverse("analytics:dashboard"))
        content = response.content.decode()
        self.assertEqual(content.count(">Test Deadlift Group<"), 1)
        self.assertContains(response, "pr-exercise-group-toggle")
        self.assertContains(response, "Set volume:")
        self.assertContains(response, "Estimated")


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

    def test_chart_has_a_visible_heading_not_just_a_screen_reader_label(self):
        _log_completed_session(self.alice, self.exercise, Decimal("50"), [5], days_ago=7)
        _log_completed_session(self.alice, self.exercise, Decimal("52.5"), [5], days_ago=0)
        response = self.client.get(reverse("analytics:exercise", args=[self.exercise.pk]))
        self.assertIsNotNone(response.context["one_rm_chart"])
        self.assertContains(
            response, '<h2>Estimated <abbr tabindex="0" title="One-Rep Max">1RM</abbr> trend</h2>'
        )

    def test_404_for_another_users_private_exercise(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        bobs_exercise = Exercise.objects.create(name="Bob Only", owner=bob)
        response = self.client.get(reverse("analytics:exercise", args=[bobs_exercise.pk]))
        self.assertEqual(response.status_code, 404)
