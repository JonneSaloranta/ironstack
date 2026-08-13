from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.exercises.models import Exercise
from apps.programs.models import ExercisePrescription, Program, ProgressionMethod, Workout
from apps.records.models import PersonalRecord, PRType
from apps.workouts import services as workout_services

from .engine import ProgressionAction, calculate_progression
from .suggestions import Confidence, suggest_weight

User = get_user_model()


def _make_prescription(owner, exercise, **overrides):
    program = Program.objects.create(owner=owner, name="Test Program")
    workout = Workout.objects.create(program=program, name="Day 1")
    defaults = dict(
        workout=workout,
        exercise=exercise,
        set_count=3,
        min_reps=8,
        max_reps=12,
        target_weight=Decimal("100.00"),
        weight_increment=Decimal("2.5"),
    )
    defaults.update(overrides)
    return ExercisePrescription.objects.create(**defaults)


def _log_completed_attempt(user, workout, exercise, weight, reps_list, failed_set_index=None):
    session = workout_services.start_session(user, workout=workout)
    performed = session.performed_exercises.get(exercise=exercise)
    for i, reps in enumerate(reps_list):
        workout_services.log_set(
            performed, weight=weight, reps=reps, is_failure=(i == failed_set_index)
        )
    workout_services.complete_session(session)
    return performed


class NoHistoryTests(TestCase):
    """Every method falls back to the prescribed target when there is no
    completed history yet, per the "first recorded performance" spirit
    already exercised for PRs in Phase 5."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Bench", owner=None)

    def test_each_method_reports_insufficient_data_with_a_sensible_fallback(self):
        methods = [
            ProgressionMethod.LINEAR,
            ProgressionMethod.DOUBLE_PROGRESSION,
            ProgressionMethod.REP_RANGE,
            ProgressionMethod.MAINTENANCE,
            ProgressionMethod.RPE_RIR,
        ]
        for method in methods:
            prescription = _make_prescription(
                self.alice, self.exercise, progression_method=method
            )
            result = calculate_progression(self.alice, prescription)
            self.assertEqual(result.action, ProgressionAction.INSUFFICIENT_DATA, method)
            self.assertEqual(result.suggested_weight, Decimal("100.00"), method)


class ManualProgressionTests(TestCase):
    def test_manual_never_computes_a_recommendation(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        exercise = Exercise.objects.create(name="Test Curl", owner=None)
        prescription = _make_prescription(
            alice, exercise, progression_method=ProgressionMethod.MANUAL,
            target_weight=Decimal("20.00"),
        )
        _log_completed_attempt(alice, prescription.workout, exercise, Decimal("20"), [15, 15, 15])

        result = calculate_progression(alice, prescription)
        self.assertEqual(result.action, ProgressionAction.MANUAL)
        self.assertEqual(result.suggested_weight, Decimal("20.00"))


class LinearProgressionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Squat", owner=None)
        self.prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.LINEAR,
            min_reps=5,
            max_reps=5,
            weight_increment=Decimal("2.5"),
        )

    def test_hitting_every_rep_increases_by_the_increment(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("100"), [5, 5, 5]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.INCREASE)
        self.assertEqual(result.suggested_weight, Decimal("102.50"))

    def test_a_single_missed_session_just_maintains(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("100"), [5, 4, 5]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.MAINTAIN)
        self.assertEqual(result.suggested_weight, Decimal("100"))

    def test_two_consecutive_misses_at_the_same_weight_recommends_a_deload(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("100"), [5, 4, 5]
        )
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("100"), [4, 4, 4]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.DELOAD)
        self.assertEqual(result.suggested_weight, Decimal("90.00"))

    def test_a_failed_set_counts_as_a_miss_even_with_enough_reps(self):
        _log_completed_attempt(
            self.alice,
            self.prescription.workout,
            self.exercise,
            Decimal("100"),
            [5, 5, 5],
            failed_set_index=2,
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.MAINTAIN)

    def test_recovering_after_a_deload_starts_increasing_again(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("90"), [4, 4, 4]
        )
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("90"), [4, 4, 4]
        )
        # deload triggers here in a real flow; simulate the user then
        # succeeding at the reduced weight:
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("90"), [5, 5, 5]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.INCREASE)
        self.assertEqual(result.suggested_weight, Decimal("92.50"))


class DoubleProgressionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Row", owner=None)
        self.prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.DOUBLE_PROGRESSION,
            min_reps=8,
            max_reps=12,
            weight_increment=Decimal("2.5"),
        )

    def test_hitting_the_top_of_the_range_on_one_session_increases_immediately(self):
        # Matches docs/PROGRESSION.md's literal example: 12/12/12 @ 80kg -> 82.5kg.
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("80"), [12, 12, 12]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.INCREASE)
        self.assertEqual(result.suggested_weight, Decimal("82.50"))

    def test_within_range_but_below_the_top_maintains(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("80"), [9, 9, 9]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.MAINTAIN)
        self.assertEqual(result.suggested_weight, Decimal("80"))

    def test_missing_the_bottom_of_the_range_twice_recommends_a_deload(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("80"), [6, 6, 6]
        )
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("80"), [6, 6, 6]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.DELOAD)
        self.assertEqual(result.suggested_weight, Decimal("72.00"))


class RepRangeProgressionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Pulldown", owner=None)
        self.prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.REP_RANGE,
            min_reps=8,
            max_reps=12,
            weight_increment=Decimal("2.5"),
        )

    def test_one_session_at_the_top_of_the_range_only_maintains(self):
        """Unlike double progression, rep_range waits for a repeated
        trend — see docs/PROGRESSION.md "recent performance trend should
        be considered"."""
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("50"), [12, 12, 12]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.MAINTAIN)

    def test_two_consecutive_sessions_at_the_top_of_the_range_increases(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("50"), [12, 12, 12]
        )
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("50"), [12, 12, 12]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.INCREASE)
        self.assertEqual(result.suggested_weight, Decimal("52.50"))

    def test_a_gap_at_a_different_weight_resets_the_streak(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("50"), [12, 12, 12]
        )
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("52.5"), [8, 8, 8]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.MAINTAIN)


class PercentageBasedProgressionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Deadlift", owner=None)
        self.prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.PERCENTAGE_BASED,
            percentage_target=Decimal("80"),
        )

    def test_no_percentage_target_is_insufficient_data(self):
        prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.PERCENTAGE_BASED,
            percentage_target=None,
        )
        result = calculate_progression(self.alice, prescription)
        self.assertEqual(result.action, ProgressionAction.INSUFFICIENT_DATA)

    def test_no_1rm_available_is_insufficient_data(self):
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.INSUFFICIENT_DATA)

    def test_manual_one_rm_takes_priority(self):
        result = calculate_progression(
            self.alice, self.prescription, manual_one_rm=Decimal("200")
        )
        self.assertEqual(result.action, ProgressionAction.CALCULATED)
        self.assertEqual(result.one_rm_source, "manual")
        self.assertEqual(result.suggested_weight, Decimal("160.00"))

    def test_falls_back_to_the_latest_estimated_1rm_pr(self):
        PersonalRecord.objects.create(
            user=self.alice,
            exercise=self.exercise,
            record_type=PRType.ESTIMATED_1RM,
            value=Decimal("150.00"),
            weight=Decimal("130"),
            reps=5,
            achieved_at=workout_services.timezone.now(),
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.one_rm_source, "latest_pr")
        self.assertEqual(result.suggested_weight, Decimal("120.00"))

    def test_falls_back_to_a_live_estimate_from_the_most_recent_set_when_no_pr_exists(self):
        # Log a set directly (bypassing apps.records' signal-free detection
        # path) so there is history but deliberately no stored PR.
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("100"), [5]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.one_rm_source, "estimated")
        # Epley: 100 * (1 + 5/30) = 116.666... -> 116.67; 80% of that = 93.34.
        self.assertEqual(result.suggested_weight, Decimal("93.34"))


class RpeRirProgressionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test OHP", owner=None)
        self.prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.RPE_RIR,
            target_rir=2,
            weight_increment=Decimal("2.5"),
        )

    def _log_with_rir(self, weight, rir):
        session = workout_services.start_session(self.alice, workout=self.prescription.workout)
        performed = session.performed_exercises.get(exercise=self.exercise)
        workout_services.log_set(performed, weight=weight, reps=8, rir=rir)
        workout_services.complete_session(session)

    def test_no_rir_logged_is_insufficient_data(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("60"), [8, 8, 8]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.INSUFFICIENT_DATA)

    def test_more_reserve_than_targeted_increases(self):
        # docs/PROGRESSION.md example: target RIR 2, actual 4 -> increase.
        self._log_with_rir(Decimal("60"), rir=4)
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.INCREASE)
        self.assertEqual(result.suggested_weight, Decimal("62.50"))

    def test_matching_the_target_rir_maintains(self):
        self._log_with_rir(Decimal("60"), rir=2)
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.MAINTAIN)

    def test_much_closer_to_failure_than_targeted_decreases(self):
        # docs/PROGRESSION.md example: target RIR 2, actual 0 -> reduce.
        self._log_with_rir(Decimal("60"), rir=0)
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.DECREASE)
        self.assertEqual(result.suggested_weight, Decimal("54.00"))


class MaintenanceProgressionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Plank", owner=None)
        self.prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.MAINTENANCE,
            min_reps=8,
            max_reps=10,
        )

    def test_normal_performance_just_repeats_the_weight(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("40"), [9, 9, 9]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.MAINTAIN)
        self.assertEqual(result.suggested_weight, Decimal("40"))

    def test_repeated_failure_still_recommends_easing_off(self):
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("40"), [5, 5, 5]
        )
        _log_completed_attempt(
            self.alice, self.prescription.workout, self.exercise, Decimal("40"), [5, 5, 5]
        )
        result = calculate_progression(self.alice, self.prescription)
        self.assertEqual(result.action, ProgressionAction.DELOAD)


class DeterminismAndIsolationTests(TestCase):
    def test_same_inputs_produce_the_same_result(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        exercise = Exercise.objects.create(name="Test Lunge", owner=None)
        prescription = _make_prescription(
            alice, exercise, progression_method=ProgressionMethod.LINEAR, min_reps=5, max_reps=5
        )
        _log_completed_attempt(alice, prescription.workout, exercise, Decimal("50"), [5, 5, 5])

        first = calculate_progression(alice, prescription)
        second = calculate_progression(alice, prescription)
        self.assertEqual(first, second)

    def test_progression_is_scoped_per_user(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        exercise = Exercise.objects.create(name="Test Shrug", owner=None)
        alice_prescription = _make_prescription(
            alice, exercise, progression_method=ProgressionMethod.LINEAR, min_reps=5, max_reps=5
        )
        _log_completed_attempt(
            alice, alice_prescription.workout, exercise, Decimal("200"), [5, 5, 5]
        )

        bob_prescription = _make_prescription(
            bob, exercise, progression_method=ProgressionMethod.LINEAR, min_reps=5, max_reps=5
        )
        result = calculate_progression(bob, bob_prescription)
        # Bob has no history of his own -> insufficient data, unaffected
        # by Alice's numbers.
        self.assertEqual(result.action, ProgressionAction.INSUFFICIENT_DATA)


class WeightSuggestionEngineTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Pulldown", owner=None)

    def test_insufficient_history_is_low_confidence_and_falls_back_to_the_prescription(self):
        prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.LINEAR,
            target_weight=Decimal("40.00"),
        )
        suggestion = suggest_weight(self.alice, prescription)
        self.assertEqual(suggestion.confidence, Confidence.LOW)
        self.assertEqual(suggestion.suggested_weight, Decimal("40.00"))
        self.assertTrue(suggestion.reason)  # explainability: never blank

    def test_manual_progression_is_always_low_confidence(self):
        prescription = _make_prescription(
            self.alice, self.exercise, progression_method=ProgressionMethod.MANUAL
        )
        _log_completed_attempt(
            self.alice, prescription.workout, self.exercise, Decimal("40"), [10, 10, 10]
        )
        suggestion = suggest_weight(self.alice, prescription)
        self.assertEqual(suggestion.confidence, Confidence.LOW)

    def test_a_single_supporting_session_is_medium_confidence(self):
        prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.LINEAR,
            min_reps=5,
            max_reps=5,
        )
        _log_completed_attempt(
            self.alice, prescription.workout, self.exercise, Decimal("40"), [5, 5, 5]
        )
        suggestion = suggest_weight(self.alice, prescription)
        self.assertEqual(suggestion.action, ProgressionAction.INCREASE)
        self.assertEqual(suggestion.confidence, Confidence.MEDIUM)

    def test_the_documented_increase_example_is_high_confidence(self):
        """docs/SMART_SUGGESTIONS.md's literal example: "You reached the
        top of the target rep range in the last two sessions at 80 kg" ->
        82.5 kg x 8-10. Exercised here via rep_range progression, which is
        exactly the two-consecutive-sessions rule."""
        prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.REP_RANGE,
            min_reps=8,
            max_reps=10,
            weight_increment=Decimal("2.5"),
        )
        for _ in range(2):
            _log_completed_attempt(
                self.alice, prescription.workout, self.exercise, Decimal("80"), [10, 10, 10]
            )

        suggestion = suggest_weight(self.alice, prescription)
        self.assertEqual(suggestion.action, ProgressionAction.INCREASE)
        self.assertEqual(suggestion.suggested_weight, Decimal("82.50"))
        self.assertEqual(suggestion.target_min_reps, 8)
        self.assertEqual(suggestion.target_max_reps, 10)
        self.assertEqual(suggestion.confidence, Confidence.HIGH)
        self.assertTrue(suggestion.reason)

    def test_percentage_based_confidence_reflects_the_one_rm_source(self):
        prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.PERCENTAGE_BASED,
            percentage_target=Decimal("80"),
        )

        manual = suggest_weight(self.alice, prescription, manual_one_rm=Decimal("100"))
        self.assertEqual(manual.confidence, Confidence.HIGH)
        self.assertEqual(manual.one_rm_source, "manual")

        PersonalRecord.objects.create(
            user=self.alice,
            exercise=self.exercise,
            record_type=PRType.ESTIMATED_1RM,
            value=Decimal("120.00"),
            weight=Decimal("100"),
            reps=5,
            achieved_at=workout_services.timezone.now(),
        )
        from_pr = suggest_weight(self.alice, prescription)
        self.assertEqual(from_pr.confidence, Confidence.HIGH)
        self.assertEqual(from_pr.one_rm_source, "latest_pr")

    def test_estimated_one_rm_source_is_medium_confidence(self):
        prescription = _make_prescription(
            self.alice,
            self.exercise,
            progression_method=ProgressionMethod.PERCENTAGE_BASED,
            percentage_target=Decimal("80"),
        )
        _log_completed_attempt(
            self.alice, prescription.workout, self.exercise, Decimal("100"), [5]
        )
        suggestion = suggest_weight(self.alice, prescription)
        self.assertEqual(suggestion.one_rm_source, "estimated")
        self.assertEqual(suggestion.confidence, Confidence.MEDIUM)

    def test_suggestion_is_deterministic_for_the_same_inputs(self):
        prescription = _make_prescription(
            self.alice, self.exercise, progression_method=ProgressionMethod.LINEAR
        )
        _log_completed_attempt(
            self.alice, prescription.workout, self.exercise, Decimal("40"), [10, 10, 10]
        )
        first = suggest_weight(self.alice, prescription)
        second = suggest_weight(self.alice, prescription)
        self.assertEqual(first, second)

    def test_user_can_always_override_the_suggestion(self):
        """docs/SMART_SUGGESTIONS.md: "Never prevent a user from entering
        a different value." The suggestion is only ever a form default —
        asserted end-to-end against the logging view in
        apps.workouts.tests; here we just confirm nothing in the returned
        Suggestion is mandatory/blocking (e.g. no validation-affecting
        state), i.e. it is plain data the caller is free to disregard."""
        prescription = _make_prescription(
            self.alice, self.exercise, progression_method=ProgressionMethod.MANUAL
        )
        suggestion = suggest_weight(self.alice, prescription)
        self.assertIsInstance(suggestion.suggested_weight, (Decimal, type(None)))
