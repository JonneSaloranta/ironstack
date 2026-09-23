"""Domain tests for apps.stretching — models, seed data and services.
View/permission tests live in test_views.py."""

import json
from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.exercises.models import Exercise, MuscleGroup
from apps.workouts import services as workout_services

from . import i18n_content, services
from .models import (
    PerformedStretchStatus,
    RoutineItem,
    Stretch,
    StretchRoutine,
    StretchSession,
    StretchSessionStatus,
)

User = get_user_model()

SEED_PATH = Path(__file__).resolve().parent / "seed_data" / "stretches.json"


def make_user(username="alice"):
    return User.objects.create_user(username=username, password="s3cret-pass")


class StretchingSeedTests(TestCase):
    def test_seed_creates_system_stretches_and_routines(self):
        data = json.loads(SEED_PATH.read_text())
        self.assertEqual(
            set(Stretch.objects.filter(owner=None).values_list("name", flat=True)),
            {entry["name"] for entry in data["stretches"]},
        )
        self.assertEqual(
            set(StretchRoutine.objects.filter(owner=None).values_list("name", flat=True)),
            {entry["name"] for entry in data["routines"]},
        )

    def test_every_seeded_stretch_has_muscle_groups_and_instructions(self):
        for stretch in Stretch.objects.filter(owner=None):
            with self.subTest(stretch=stretch.name):
                self.assertTrue(stretch.muscle_groups.exists())
                self.assertTrue(stretch.instructions)

    def test_seeded_routines_keep_their_item_order(self):
        routine = StretchRoutine.objects.get(name="Lower Body Cool-Down")
        self.assertEqual(
            [item.stretch.name for item in routine.items.all()],
            [
                "Standing Quad Stretch",
                "Kneeling Hip Flexor Stretch",
                "Standing Hamstring Stretch",
                "Figure-Four Stretch",
                "Wall Calf Stretch",
            ],
        )

    def test_every_seeded_string_is_in_the_translation_catalog(self):
        data = json.loads(SEED_PATH.read_text())
        catalog = {
            str(text)
            for text in i18n_content.STRETCH_NAMES
            + i18n_content.ROUTINE_TEXTS
            + i18n_content.STRETCH_INSTRUCTIONS
        }
        expected = set()
        for entry in data["stretches"]:
            expected |= {entry["name"], entry["instructions"]}
        for entry in data["routines"]:
            expected |= {entry["name"], entry["description"]}
        self.assertEqual(expected - catalog, set())


class StretchModelTests(TestCase):
    def test_two_users_can_each_have_a_custom_stretch_with_the_same_name(self):
        Stretch.objects.create(name="Lizard", owner=make_user("alice"))
        Stretch.objects.create(name="Lizard", owner=make_user("bob"))

    def test_system_stretch_names_are_unique(self):
        with self.assertRaises(IntegrityError):
            Stretch.objects.create(name="Cat-Cow", owner=None)

    def test_only_one_in_progress_session_per_user(self):
        alice = make_user()
        StretchSession.objects.create(user=alice, status=StretchSessionStatus.IN_PROGRESS)
        with self.assertRaises(IntegrityError):
            StretchSession.objects.create(user=alice, status=StretchSessionStatus.IN_PROGRESS)


class VisibilityTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")

    def test_system_and_own_stretches_but_not_others(self):
        own = Stretch.objects.create(name="Mine", owner=self.alice)
        other = Stretch.objects.create(name="Bobs", owner=self.bob)
        visible = services.visible_stretches(self.alice)
        self.assertIn(own, visible)
        self.assertNotIn(other, visible)
        self.assertIn(Stretch.objects.get(name="Cat-Cow"), visible)

    def test_inactive_stretches_hidden_unless_asked(self):
        retired = Stretch.objects.create(name="Old", owner=self.alice, active=False)
        self.assertNotIn(retired, services.visible_stretches(self.alice))
        self.assertIn(retired, services.visible_stretches(self.alice, include_inactive=True))

    def test_routines_follow_the_same_rule(self):
        own = StretchRoutine.objects.create(name="Mine", owner=self.alice)
        other = StretchRoutine.objects.create(name="Bobs", owner=self.bob)
        visible = services.visible_routines(self.alice)
        self.assertIn(own, visible)
        self.assertNotIn(other, visible)


class DurationMathTests(TestCase):
    def test_single_hold(self):
        self.assertEqual(
            services.item_seconds(hold_seconds=30, sets=1, per_side=False, rest_seconds=10), 30
        )

    def test_per_side_doubles_holds_with_rest_between(self):
        # left 30 + rest 10 + right 30
        self.assertEqual(
            services.item_seconds(hold_seconds=30, sets=1, per_side=True, rest_seconds=10), 70
        )

    def test_sets_and_sides(self):
        # 2 sets × 2 sides = 4 holds, 3 rests
        self.assertEqual(
            services.item_seconds(hold_seconds=20, sets=2, per_side=True, rest_seconds=5), 95
        )

    def test_routine_seconds_sums_items(self):
        routine = StretchRoutine.objects.create(name="R", owner=make_user())
        services.add_routine_item(routine, Stretch.objects.get(name="Cat-Cow"))  # 30
        services.add_routine_item(
            routine, Stretch.objects.get(name="Standing Quad Stretch")
        )  # 30 + 10 + 30
        self.assertEqual(services.routine_seconds(routine), 100)


class RoutineEditingTests(TestCase):
    def setUp(self):
        self.alice = make_user()
        self.routine = StretchRoutine.objects.create(name="R", owner=self.alice)
        self.a = services.add_routine_item(self.routine, Stretch.objects.get(name="Cat-Cow"))
        self.b = services.add_routine_item(self.routine, Stretch.objects.get(name="Cobra Stretch"))

    def test_add_uses_stretch_default_hold_and_appends(self):
        child = Stretch.objects.get(name="Child's Pose")
        item = services.add_routine_item(self.routine, child)
        self.assertEqual(item.hold_seconds, child.default_hold_seconds)
        self.assertEqual(item.order, 2)

    def test_move_swaps_neighbours(self):
        services.move_routine_item(self.b, -1)
        self.assertEqual(list(self.routine.items.all()), [self.b, self.a])

    def test_move_past_the_end_is_a_no_op(self):
        services.move_routine_item(self.a, -1)
        self.assertEqual(list(self.routine.items.all()), [self.a, self.b])

    def test_copy_system_routine_creates_an_owned_copy(self):
        system = StretchRoutine.objects.get(name="Lower Body Cool-Down")
        copy = services.copy_routine(system, self.alice)
        self.assertEqual(copy.owner, self.alice)
        self.assertEqual(copy.items.count(), system.items.count())
        again = services.copy_routine(system, self.alice)
        self.assertNotEqual(copy.name, again.name)


class SessionTests(TestCase):
    def setUp(self):
        self.alice = make_user()
        self.routine = StretchRoutine.objects.create(name="Evening", owner=self.alice)
        self.quad = Stretch.objects.get(name="Standing Quad Stretch")
        self.item = services.add_routine_item(self.routine, self.quad, hold_seconds=40, sets=2)

    def test_start_snapshots_routine_items(self):
        session = services.start_session(self.alice, routine=self.routine)
        performed = session.performed_stretches.get()
        self.assertEqual(session.name, "Evening")
        self.assertEqual(performed.stretch_name, "Standing Quad Stretch")
        self.assertTrue(performed.per_side)
        self.assertEqual((performed.target_hold_seconds, performed.sets), (40, 2))

    def test_editing_routine_later_never_changes_the_session(self):
        session = services.start_session(self.alice, routine=self.routine)
        self.item.hold_seconds = 90
        self.item.save()
        self.routine.name = "Renamed"
        self.routine.save()
        RoutineItem.objects.filter(pk=self.item.pk).delete()
        session.refresh_from_db()
        performed = session.performed_stretches.get()
        self.assertEqual(session.name, "Evening")
        self.assertEqual(performed.target_hold_seconds, 40)

    def test_start_from_plain_stretch_list(self):
        session = services.start_session(
            self.alice, stretches=[self.quad], name="Cool-down"
        )
        performed = session.performed_stretches.get()
        self.assertEqual(performed.target_hold_seconds, self.quad.default_hold_seconds)
        self.assertEqual(performed.rest_seconds, services.AD_HOC_REST_SECONDS)

    def test_cannot_start_a_second_session(self):
        first = services.start_session(self.alice, routine=self.routine)
        with self.assertRaises(services.SessionAlreadyInProgress) as ctx:
            services.start_session(self.alice, routine=self.routine)
        self.assertEqual(ctx.exception.session, first)

    def test_mark_performed_done_and_skipped(self):
        session = services.start_session(self.alice, routine=self.routine)
        performed = session.performed_stretches.get()
        services.mark_performed(performed, done=True, actual_seconds=75)
        self.assertEqual(performed.status, PerformedStretchStatus.DONE)
        self.assertEqual(performed.actual_seconds, 75)
        services.mark_performed(performed, done=False, actual_seconds=75)
        self.assertEqual(performed.status, PerformedStretchStatus.SKIPPED)
        self.assertIsNone(performed.actual_seconds)

    def test_complete_records_wall_clock_duration(self):
        session = services.start_session(self.alice, routine=self.routine)
        session.started_at = timezone.now() - timedelta(minutes=12)
        session.save()
        services.complete_session(session)
        self.assertEqual(session.status, StretchSessionStatus.COMPLETED)
        self.assertAlmostEqual(session.duration.total_seconds(), 720, delta=5)

    def test_abandon(self):
        session = services.start_session(self.alice, routine=self.routine)
        services.abandon_session(session)
        self.assertEqual(session.status, StretchSessionStatus.ABANDONED)
        self.assertIsNone(services.active_session(self.alice))

    def test_quick_log_is_completed_immediately(self):
        session = services.quick_log(
            self.alice, date=date(2026, 9, 1), duration=timedelta(minutes=15)
        )
        self.assertEqual(session.status, StretchSessionStatus.COMPLETED)
        self.assertFalse(session.performed_stretches.exists())


class TimerStepsTests(TestCase):
    def setUp(self):
        self.alice = make_user()
        self.routine = StretchRoutine.objects.create(name="R", owner=self.alice)

    def test_per_side_stretch_with_rest(self):
        services.add_routine_item(
            self.routine, Stretch.objects.get(name="Standing Quad Stretch"),
            hold_seconds=30, rest_seconds=5,
        )
        steps = services.timer_steps(services.start_session(self.alice, routine=self.routine))
        self.assertEqual(
            [(step["kind"], step["side"], step["seconds"]) for step in steps],
            [("hold", "left", 30), ("rest", "left", 5), ("hold", "right", 30)],
        )
        self.assertEqual([step["last_of_stretch"] for step in steps], [False, False, True])

    def test_no_rest_step_when_rest_is_zero(self):
        services.add_routine_item(
            self.routine, Stretch.objects.get(name="Cat-Cow"), sets=2, rest_seconds=0
        )
        steps = services.timer_steps(services.start_session(self.alice, routine=self.routine))
        self.assertEqual([step["kind"] for step in steps], ["hold", "hold"])
        self.assertEqual([step["set"] for step in steps], [1, 2])

    def test_finished_stretches_are_left_out(self):
        services.add_routine_item(self.routine, Stretch.objects.get(name="Cat-Cow"))
        services.add_routine_item(self.routine, Stretch.objects.get(name="Cobra Stretch"))
        session = services.start_session(self.alice, routine=self.routine)
        first, second = session.performed_stretches.all()
        services.mark_performed(first, done=True)
        steps = services.timer_steps(session)
        self.assertEqual({step["performed"] for step in steps}, {second.pk})


class CooldownSuggestionTests(TestCase):
    def setUp(self):
        self.alice = make_user()

    def _workout_with(self, *exercise_names):
        session = workout_services.start_session(self.alice)
        for name in exercise_names:
            workout_services.add_performed_exercise(session, Exercise.objects.get(name=name))
        return session

    def test_no_exercises_means_no_suggestion(self):
        self.assertIsNone(services.suggest_cooldown(self._workout_with()))

    def test_leg_day_suggests_the_lower_body_routine(self):
        workout = self._workout_with("Barbell Back Squat", "Leg Curl", "Calf Raise")
        suggestion = services.suggest_cooldown(workout)
        self.assertEqual(suggestion.routine.name, "Lower Body Cool-Down")

    def test_falls_back_to_a_stretch_list_when_no_routine_covers_enough(self):
        StretchRoutine.objects.filter(owner=None).update(active=False)
        workout = self._workout_with("Barbell Back Squat")
        suggestion = services.suggest_cooldown(workout)
        self.assertIsNone(suggestion.routine)
        self.assertTrue(suggestion.stretches)
        trained = set(services.trained_muscle_groups(workout))
        for stretch in suggestion.stretches:
            self.assertTrue(set(stretch.muscle_groups.all()) & trained)

    def test_fallback_list_is_static_stretches_only(self):
        StretchRoutine.objects.filter(owner=None).update(active=False)
        workout = self._workout_with("Barbell Back Squat", "Barbell Bench Press")
        suggestion = services.suggest_cooldown(workout)
        self.assertTrue(all(stretch.kind == "static" for stretch in suggestion.stretches))

    def test_muscle_with_no_stretch_at_all_gives_no_suggestion(self):
        StretchRoutine.objects.filter(owner=None).update(active=False)
        lonely = MuscleGroup.objects.create(name="Lonely")
        exercise = Exercise.objects.create(name="Lonely Lift")
        exercise.primary_muscle_groups.add(lonely)
        workout = workout_services.start_session(self.alice)
        workout_services.add_performed_exercise(workout, exercise)
        self.assertIsNone(services.suggest_cooldown(workout))


class StatsTests(TestCase):
    def setUp(self):
        self.alice = make_user()
        self.today = date(2026, 9, 23)  # a Wednesday

    def _log(self, day, minutes):
        return services.quick_log(self.alice, date=day, duration=timedelta(minutes=minutes))

    def test_streak_counts_consecutive_days_ending_today(self):
        for offset in (0, 1, 2, 4):
            self._log(self.today - timedelta(days=offset), 10)
        self.assertEqual(services.summarize(self.alice, today=self.today).streak_days, 3)

    def test_streak_survives_until_today_is_done(self):
        self._log(self.today - timedelta(days=1), 10)
        self._log(self.today - timedelta(days=2), 10)
        self.assertEqual(services.summarize(self.alice, today=self.today).streak_days, 2)

    def test_summary_ignores_abandoned_sessions_and_other_users(self):
        self._log(self.today, 10)
        StretchSession.objects.create(
            user=self.alice, status=StretchSessionStatus.ABANDONED, date=self.today,
            duration=timedelta(minutes=99),
        )
        services.quick_log(make_user("bob"), date=self.today, duration=timedelta(minutes=50))
        summary = services.summarize(self.alice, today=self.today)
        self.assertEqual(summary.count, 1)
        self.assertEqual(summary.total_duration, timedelta(minutes=10))

    def test_weekly_minutes_includes_empty_weeks(self):
        self._log(self.today, 10)
        self._log(self.today - timedelta(days=2), 5)  # same ISO week (Monday)
        rows = services.weekly_minutes(self.alice, weeks=3, today=self.today)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1], (date(2026, 9, 21), 15))
        self.assertEqual(rows[0][1], 0)

    def test_calendar_minutes_sums_per_day(self):
        self._log(date(2026, 9, 5), 10)
        self._log(date(2026, 9, 5), 5)
        self._log(date(2026, 8, 31), 20)
        self.assertEqual(
            services.calendar_minutes(self.alice, 2026, 9), {date(2026, 9, 5): 15}
        )
