"""apps.workouts.set_timing - set cycle estimates (docs/ANALYTICS.md
"Set cycle time")."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.templatetags.core_extras import seconds_duration
from apps.exercises.models import Exercise
from apps.programs.models import ExercisePrescription, Program, Workout

from . import services, set_timing
from .models import ExerciseSet, PerformedExercise, WorkoutSession, WorkoutSessionStatus

User = get_user_model()


def _log(user, sets, status=WorkoutSessionStatus.COMPLETED, start=None):
    """A session from `sets`: (exercise, reps, seconds since the previous
    set) tuples, optionally a 4th dict - `warmup`, `created_delay`
    (created_at minus performed_at), `created_gap` (created_at only that
    far after the previous set's)."""
    start = start or timezone.now() - timedelta(days=1)
    session = WorkoutSession.objects.create(user=user, status=status, started_at=start)
    performed, numbers = {}, {}
    at = created = start
    for item in sets:
        exercise, reps, gap = item[:3]
        options = item[3] if len(item) > 3 else {}
        if exercise.pk not in performed:
            performed[exercise.pk] = PerformedExercise.objects.create(
                session=session, exercise=exercise, order=len(performed)
            )
        numbers[exercise.pk] = numbers.get(exercise.pk, 0) + 1
        at += timedelta(seconds=gap)
        created = (
            created + timedelta(seconds=options["created_gap"])
            if "created_gap" in options
            else at + timedelta(seconds=options.get("created_delay", 0))
        )
        logged = ExerciseSet.objects.create(
            performed_exercise=performed[exercise.pk],
            set_number=numbers[exercise.pk],
            weight=Decimal("50"),
            reps=reps,
            is_warmup=options.get("warmup", False),
            performed_at=at,
        )
        ExerciseSet.objects.filter(pk=logged.pk).update(created_at=created)
    return session


def _cycles(user=None):
    cycles, _transitions = set_timing._gaps(user=user)
    return sorted(c["seconds"] for c in cycles)


class RepBucketTests(TestCase):
    def test_bucket_edges(self):
        labels = [
            set_timing.rep_bucket_label(set_timing.rep_bucket(r))
            for r in (1, 3, 4, 6, 7, 10, 11, 15, 16, 40)
        ]
        self.assertEqual(
            labels, ["1-3", "1-3", "4-6", "4-6", "7-10", "7-10", "11-15", "11-15", "16+", "16+"]
        )


class GapFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.squat = Exercise.objects.create(name="Squat")
        self.curl = Exercise.objects.create(name="Curl", movement_type="isolation")

    def test_cycle_is_the_gap_between_two_sets_of_one_exercise(self):
        _log(self.user, [(self.squat, 5, 0), (self.squat, 5, 150), (self.squat, 5, 170)])
        self.assertEqual(_cycles(), [150, 170])

    def test_hard_limits_drop_misfires_and_forgotten_logs(self):
        _log(
            self.user,
            [(self.squat, 5, 0), (self.squat, 5, 6), (self.squat, 5, 120), (self.squat, 5, 1500)],
        )
        self.assertEqual(_cycles(), [120])

    def test_sets_typed_in_afterwards_are_dropped(self):
        # Logged 2 s apart in reality, even though the times say 2 min.
        _log(self.user, [(self.squat, 5, 0), (self.squat, 5, 120, {"created_gap": 2})])
        self.assertEqual(_cycles(), [])

    def test_a_set_whose_time_was_edited_is_dropped(self):
        _log(self.user, [(self.squat, 5, 0), (self.squat, 5, 120, {"created_delay": 600})])
        self.assertEqual(_cycles(), [])

    def test_supersets_are_dropped(self):
        _log(
            self.user,
            [(self.squat, 5, 0), (self.curl, 10, 60), (self.squat, 5, 60), (self.curl, 10, 60)],
        )
        self.assertEqual(_cycles(), [])

    def test_warm_up_sets_are_dropped(self):
        _log(
            self.user,
            [(self.squat, 5, 0, {"warmup": True}), (self.squat, 5, 90), (self.squat, 5, 150)],
        )
        self.assertEqual(_cycles(), [150])

    def test_only_completed_workouts_count(self):
        _log(
            self.user,
            [(self.squat, 5, 0), (self.squat, 5, 150)],
            status=WorkoutSessionStatus.ABANDONED,
        )
        self.assertEqual(_cycles(), [])

    def test_first_set_of_the_next_exercise_is_a_transition(self):
        _log(self.user, [(self.squat, 5, 0), (self.squat, 5, 150), (self.curl, 10, 240)])
        cycles, transitions = set_timing._gaps()
        self.assertEqual([c["seconds"] for c in cycles], [150])
        self.assertEqual([t["seconds"] for t in transitions], [240])


def _samples(seconds, user=1, start=None):
    start = start or timezone.now()
    return [
        {"seconds": s, "user": user, "performed_at": start - timedelta(minutes=i)}
        for i, s in enumerate(seconds)
    ]


class SummarizeTests(TestCase):
    def test_median_and_quartiles(self):
        stats = set_timing.summarize(_samples([100, 110, 120, 130, 140]))
        self.assertEqual((stats.q1, stats.median, stats.q3), (110, 120, 130))
        self.assertEqual(stats.spread, 10)
        self.assertEqual(stats.sample_size, 5)

    def test_outliers_off_the_curve_are_dropped_on_a_log_scale(self):
        normal = [110 + (i % 5) * 5 for i in range(30)]
        stats = set_timing.summarize(_samples(normal + [15, 1100]))
        self.assertEqual(stats.sample_size, 30)
        self.assertEqual(stats.median, 120)

    def test_identical_gaps_dont_collapse_the_fences(self):
        stats = set_timing.summarize(_samples([120] * 20 + [130, 140]))
        self.assertEqual(stats.sample_size, 22)

    def test_a_long_normal_rest_is_not_an_outlier(self):
        # Right-skewed like real rests: the log fences keep the long tail.
        values = [90, 100, 110, 120, 130, 150, 180, 240]
        self.assertEqual(set_timing.summarize(_samples(values)).sample_size, len(values))

    def test_each_user_counts_only_their_latest_sets(self):
        heavy = _samples([90 + i % 5 * 10 for i in range(80)], user=1)
        light = _samples([100 + i % 5 * 10 for i in range(10)], user=2)
        stats = set_timing.summarize(heavy + light)
        self.assertEqual(stats.sample_size, set_timing.PER_USER_CAP + 10)
        self.assertEqual(stats.user_count, 2)


def _stats(median, n=30, users=1):
    return set_timing.GapStats(
        median=median, q1=median - 10, q3=median + 20, sample_size=n, user_count=users
    )


class EstimateTests(TestCase):
    def setUp(self):
        self.squat = Exercise.objects.create(name="Squat")
        self.bucket = set_timing.rep_bucket(5)

    def test_most_specific_group_with_enough_data_wins(self):
        stats = {
            (set_timing.SOURCE_EXERCISE_REPS, (self.squat.pk, self.bucket)): _stats(150),
            (set_timing.SOURCE_EXERCISE, self.squat.pk): _stats(140),
        }
        estimate = set_timing.estimate_cycle(self.squat, 5, stats)
        self.assertEqual(estimate.source, set_timing.SOURCE_EXERCISE_REPS)
        self.assertEqual(estimate.stats.median, 150)

    def test_falls_back_when_a_group_is_too_small(self):
        stats = {
            (set_timing.SOURCE_EXERCISE_REPS, (self.squat.pk, self.bucket)): _stats(150, n=5),
            (set_timing.SOURCE_EXERCISE, self.squat.pk): _stats(140, n=5),
            (set_timing.SOURCE_MOVEMENT_REPS, ("compound", self.bucket)): _stats(130),
        }
        self.assertEqual(
            set_timing.estimate_cycle(self.squat, 5, stats).source,
            set_timing.SOURCE_MOVEMENT_REPS,
        )

    def test_one_user_is_enough(self):
        stats = {(set_timing.SOURCE_EXERCISE, self.squat.pk): _stats(140, users=1)}
        self.assertEqual(
            set_timing.estimate_cycle(self.squat, 5, stats).source, set_timing.SOURCE_EXERCISE
        )

    def test_default_formula_without_data(self):
        estimate = set_timing.estimate_cycle(self.squat, 10, {})
        self.assertTrue(estimate.is_default)
        self.assertEqual(estimate.stats.median, 120 + 3 * 10)

    def test_estimate_from_real_logs(self):
        users = [User.objects.create_user(username=f"u{i}", password="x") for i in range(2)]
        for user in users:
            sets = [(self.squat, 5, 0)] + [(self.squat, 5, 140 + i % 3 * 10) for i in range(11)]
            _log(user, sets)
        set_timing.clear_cache()
        estimate = set_timing.estimate_cycle(self.squat, 5)
        self.assertEqual(estimate.source, set_timing.SOURCE_EXERCISE_REPS)
        self.assertEqual(estimate.stats.sample_size, 22)
        self.assertEqual(estimate.stats.median, 150)


class WorkoutEstimateTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="owner", password="x")
        program = Program.objects.create(owner=owner, name="P")
        self.workout = Workout.objects.create(program=program, name="Day")
        self.squat = Exercise.objects.create(name="Squat")
        self.curl = Exercise.objects.create(name="Curl", movement_type="isolation")
        ExercisePrescription.objects.create(
            workout=self.workout, exercise=self.squat, set_count=3, min_reps=5, max_reps=5
        )
        ExercisePrescription.objects.create(
            workout=self.workout, exercise=self.curl, set_count=2, min_reps=10, max_reps=12, order=1
        )
        self.stats = {
            (set_timing.SOURCE_EXERCISE, self.squat.pk): _stats(150),
            (set_timing.SOURCE_EXERCISE, self.curl.pk): _stats(90),
            (set_timing.SOURCE_TRANSITION, None): _stats(200),
        }

    def test_transition_into_each_exercise_plus_a_cycle_per_further_set(self):
        estimate = set_timing.estimate_workout(self.workout, self.stats)
        # q1 = median - 10, q3 = median + 20 (see _stats).
        self.assertEqual(estimate.low, 2 * 190 + 2 * 140 + 1 * 80)
        self.assertEqual(estimate.high, 2 * 220 + 2 * 170 + 1 * 110)
        self.assertEqual(estimate.middle, (estimate.low + estimate.high) / 2)
        self.assertFalse(estimate.rough)

    def test_rough_when_any_part_has_no_data(self):
        del self.stats[(set_timing.SOURCE_EXERCISE, self.curl.pk)]
        self.assertTrue(set_timing.estimate_workout(self.workout, self.stats).rough)

    def test_no_estimate_without_exercises(self):
        empty = Workout.objects.create(program=self.workout.program, name="Empty")
        self.assertIsNone(set_timing.estimate_workout(empty, self.stats))

    def test_remaining_counts_only_unlogged_sets(self):
        user = User.objects.create_user(username="lifter", password="x")
        session = services.start_session(user, self.workout)
        squat = session.performed_exercises.get(exercise=self.squat)
        services.log_set(squat, weight=Decimal("100"), reps=5)
        remaining = set_timing.estimate_remaining(session.performed_exercises.all(), self.stats)
        # Squat: 2 cycles left. Curl: not started - transition + 1 cycle.
        self.assertEqual(remaining.low, 2 * 140 + 190 + 80)

    def test_nothing_remaining_once_all_sets_are_logged(self):
        user = User.objects.create_user(username="lifter", password="x")
        session = services.start_session(user, self.workout)
        for performed in session.performed_exercises.all():
            for _ in range(performed.set_count):
                services.log_set(performed, weight=Decimal("10"), reps=5)
        self.assertIsNone(
            set_timing.estimate_remaining(session.performed_exercises.all(), self.stats)
        )


class SecondsDurationFilterTests(TestCase):
    def test_formats(self):
        self.assertEqual(seconds_duration(42), "42 s")
        self.assertEqual(seconds_duration(141), "2 min 20 s")
        self.assertEqual(seconds_duration(120), "2 min")
        self.assertEqual(seconds_duration(3121), "52 min")
        self.assertEqual(seconds_duration(3900), "1 h 5 min")
        self.assertEqual(seconds_duration(None), "")

    def test_whole_minutes_for_a_workout(self):
        self.assertEqual(seconds_duration(515, "min"), "9 min")
        self.assertEqual(seconds_duration(20, "min"), "1 min")


class EstimateDisplayTests(TestCase):
    def setUp(self):
        set_timing.clear_cache()
        self.user = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        self.program = Program.objects.create(owner=self.user, name="P")
        self.workout = Workout.objects.create(program=self.program, name="Day")
        self.squat = Exercise.objects.create(name="Squat")
        ExercisePrescription.objects.create(
            workout=self.workout, exercise=self.squat, set_count=3, min_reps=5, max_reps=5
        )

    def test_program_workout_card_shows_its_estimated_duration(self):
        response = self.client.get(reverse("programs:program-detail", args=[self.program.pk]))
        self.assertContains(response, "Estimated duration")
        self.assertContains(response, "rough - little data yet")

    def test_training_mode_shows_time_left(self):
        session = services.start_session(self.user, self.workout)
        response = self.client.get(reverse("workouts:session-train", args=[session.pk]))
        self.assertContains(response, "Time left")

    def test_exercise_page_shows_own_set_cycle_once_there_is_enough(self):
        url = reverse("exercises:exercise-detail", args=[self.squat.pk])
        self.assertContains(self.client.get(url), "Shown once you've logged 5 sets")
        _log(self.user, [(self.squat, 5, 0)] + [(self.squat, 5, 150)] * 6)
        response = self.client.get(url)
        self.assertContains(response, '<span class="nowrap">2 min 30 s</span>')
        self.assertNotContains(response, "Everyone")


class ExerciseEstimateTests(WorkoutEstimateTests):
    def test_one_exercise_is_a_transition_plus_further_cycles(self):
        estimate, cycle = set_timing.estimate_exercise(self.squat, 4, 5, 5, self.stats)
        self.assertEqual(cycle.stats.median, 150)
        self.assertEqual(estimate.low, 190 + 3 * 140)

    def test_workout_with_the_forms_values_replacing_the_edited_exercise(self):
        curl = self.workout.prescriptions.get(exercise=self.curl)
        estimate = set_timing.estimate_workout(
            self.workout, self.stats, replace=curl.pk, extra=(self.curl, 4, 10, 12)
        )
        # Squat 3 sets as before, curl now 4 sets instead of 2.
        self.assertEqual(estimate.low, 2 * 190 + 2 * 140 + 3 * 80)


class PrescriptionEstimateViewTests(TestCase):
    def setUp(self):
        set_timing.clear_cache()
        self.user = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        self.program = Program.objects.create(owner=self.user, name="P")
        self.workout = Workout.objects.create(program=self.program, name="Day")
        self.squat = Exercise.objects.create(name="Squat")
        self.url = reverse(
            "programs:prescription-estimate", args=[self.program.pk, self.workout.pk]
        )

    def test_estimate_follows_the_forms_values(self):
        three = self.client.get(
            self.url, {"exercise": self.squat.pk, "set_count": 3, "min_reps": 5, "max_reps": 5}
        )
        self.assertContains(three, "This exercise")
        self.assertContains(three, "Whole workout")
        # Default formula: transition 180 s + 2 x (120 + 3 x 5) s = 450 s;
        # quartiles x0.75 / x1.3 -> 338 .. 585 s -> midpoint ~8 min.
        self.assertContains(three, "~8 min")
        five = self.client.get(
            self.url, {"exercise": self.squat.pk, "set_count": 5, "min_reps": 5, "max_reps": 5}
        )
        self.assertContains(five, "~12 min")

    def test_incomplete_values_ask_for_more(self):
        response = self.client.get(self.url, {"exercise": "", "set_count": "abc"})
        self.assertContains(response, "Pick the exercise and its sets")

    def test_only_the_programs_owner(self):
        User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.login(username="bob", password="s3cret-pass")
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_edit_form_starts_with_the_saved_values(self):
        prescription = ExercisePrescription.objects.create(
            workout=self.workout, exercise=self.squat, set_count=3, min_reps=5, max_reps=5
        )
        response = self.client.get(
            reverse(
                "programs:prescription-update",
                args=[self.program.pk, self.workout.pk, prescription.pk],
            )
        )
        self.assertContains(response, 'id="prescription-estimate"')
        self.assertContains(response, f"?prescription={prescription.pk}")
        self.assertContains(response, "~8 min")
