from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.exercises.models import Exercise
from apps.workouts import services as workout_services

from . import services
from .models import PersonalRecord, PRType
from .one_rep_max import OneRepMaxCalculator

User = get_user_model()


def _log_and_check(performed_exercise, **kwargs):
    exercise_set = workout_services.log_set(performed_exercise, **kwargs)
    return services.check_and_record_prs(exercise_set)


def _new_session_exercise(user, exercise):
    session = workout_services.start_session(user, workout=None)
    return workout_services.add_performed_exercise(session, exercise)


class OneRepMaxCalculatorTests(TestCase):
    def test_epley_formula(self):
        calculator = OneRepMaxCalculator(formula="epley")
        # 100kg x 5 -> 100 * (1 + 5/30) = 116.666... -> 116.67
        self.assertEqual(calculator.estimate(Decimal("100"), 5), Decimal("116.67"))

    def test_estimate_at_one_rep_equals_the_weight_plus_a_hair(self):
        calculator = OneRepMaxCalculator()
        estimate = calculator.estimate(Decimal("100"), 1)
        self.assertGreater(estimate, Decimal("100"))

    def test_zero_reps_is_rejected(self):
        calculator = OneRepMaxCalculator()
        with self.assertRaises(ValueError):
            calculator.estimate(Decimal("100"), 0)

    def test_unknown_formula_is_rejected(self):
        with self.assertRaises(ValueError):
            OneRepMaxCalculator(formula="made-up-formula")


class PRDetectionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Bench", owner=None)
        self.performed = _new_session_exercise(self.alice, self.exercise)

    def test_first_recorded_performance_creates_every_applicable_pr(self):
        new_prs = _log_and_check(self.performed, weight=Decimal("100"), reps=5)
        types = {r.record_type for r in new_prs}
        self.assertIn(PRType.MAX_WEIGHT, types)
        self.assertIn(PRType.REP_PR, types)
        self.assertIn(PRType.ESTIMATED_1RM, types)
        self.assertIn(PRType.SET_VOLUME, types)
        self.assertIn(PRType.SESSION_VOLUME, types)
        # 5 reps qualifies for the 1RM, 3RM and 5RM milestones (>= N).
        rep_specific = [r for r in new_prs if r.record_type == PRType.REP_SPECIFIC_PR]
        self.assertEqual({r.rep_count for r in rep_specific}, {1, 3, 5})

    def test_new_max_weight_is_detected(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)
        new_prs = _log_and_check(self.performed, weight=Decimal("105"), reps=5)
        self.assertTrue(any(r.record_type == PRType.MAX_WEIGHT for r in new_prs))

    def test_tied_max_weight_is_not_a_new_pr(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)
        new_prs = _log_and_check(self.performed, weight=Decimal("100"), reps=5)
        self.assertFalse(any(r.record_type == PRType.MAX_WEIGHT for r in new_prs))

    def test_lower_weight_is_not_a_new_max(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)
        new_prs = _log_and_check(self.performed, weight=Decimal("90"), reps=5)
        self.assertFalse(any(r.record_type == PRType.MAX_WEIGHT for r in new_prs))

    def test_higher_reps_at_the_same_weight_is_a_rep_pr(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)
        new_prs = _log_and_check(self.performed, weight=Decimal("100"), reps=6)
        rep_pr = [r for r in new_prs if r.record_type == PRType.REP_PR]
        self.assertEqual(len(rep_pr), 1)
        self.assertEqual(rep_pr[0].value, 6)

    def test_higher_reps_at_a_different_weight_does_not_beat_the_rep_pr(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)
        # Different weight -> a separate rep-PR "lane", not a comparison.
        new_prs = _log_and_check(self.performed, weight=Decimal("60"), reps=20)
        rep_pr = [r for r in new_prs if r.record_type == PRType.REP_PR]
        self.assertEqual(len(rep_pr), 1)
        self.assertEqual(rep_pr[0].weight, Decimal("60"))

    def test_rep_specific_pr_tracks_heaviest_weight_for_at_least_n_reps(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)
        new_prs = _log_and_check(self.performed, weight=Decimal("110"), reps=5)
        five_rm = [
            r
            for r in new_prs
            if r.record_type == PRType.REP_SPECIFIC_PR and r.rep_count == 5
        ]
        self.assertEqual(len(five_rm), 1)
        self.assertEqual(five_rm[0].value, Decimal("110"))

    def test_rep_specific_pr_only_fires_for_milestones_actually_met(self):
        new_prs = _log_and_check(self.performed, weight=Decimal("100"), reps=4)
        rep_specific = [r for r in new_prs if r.record_type == PRType.REP_SPECIFIC_PR]
        # 4 reps qualifies for >=1 and >=3, not >=5/8/10/12.
        self.assertEqual({r.rep_count for r in rep_specific}, {1, 3})

    def test_estimated_1rm_pr_uses_the_configured_formula(self):
        new_prs = _log_and_check(self.performed, weight=Decimal("100"), reps=5)
        estimate_prs = [r for r in new_prs if r.record_type == PRType.ESTIMATED_1RM]
        self.assertEqual(len(estimate_prs), 1)
        self.assertEqual(estimate_prs[0].value, Decimal("116.67"))

    def test_estimated_1rm_pr_is_not_beaten_by_a_lower_estimate(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)  # ~116.67
        new_prs = _log_and_check(self.performed, weight=Decimal("100"), reps=3)  # ~110
        self.assertFalse(any(r.record_type == PRType.ESTIMATED_1RM for r in new_prs))

    def test_set_volume_pr(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)  # 500
        new_prs = _log_and_check(self.performed, weight=Decimal("80"), reps=8)  # 640
        volume_prs = [r for r in new_prs if r.record_type == PRType.SET_VOLUME]
        self.assertEqual(len(volume_prs), 1)
        self.assertEqual(volume_prs[0].value, Decimal("640"))

    def test_session_volume_pr_accumulates_within_a_session_without_spamming(self):
        first = _log_and_check(self.performed, weight=Decimal("100"), reps=5)  # 500
        second = _log_and_check(self.performed, weight=Decimal("100"), reps=5)  # +500=1000
        self.assertTrue(any(r.record_type == PRType.SESSION_VOLUME for r in first))
        # Second set in the SAME session updates the existing row in place
        # rather than firing another "new PR" notification.
        self.assertFalse(any(r.record_type == PRType.SESSION_VOLUME for r in second))
        record = PersonalRecord.objects.get(
            record_type=PRType.SESSION_VOLUME, exercise=self.exercise
        )
        self.assertEqual(record.value, Decimal("1000"))

    def test_a_later_session_with_more_volume_creates_its_own_session_pr(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)  # session 1: 500

        other_performed = _new_session_exercise(self.alice, self.exercise)
        new_prs = _log_and_check(other_performed, weight=Decimal("100"), reps=6)  # 600 > 500
        self.assertTrue(any(r.record_type == PRType.SESSION_VOLUME for r in new_prs))
        self.assertEqual(
            PersonalRecord.objects.filter(
                record_type=PRType.SESSION_VOLUME, exercise=self.exercise
            ).count(),
            2,
        )

    def test_a_later_session_with_less_volume_does_not_create_a_session_pr(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=10)  # session 1: 1000

        other_performed = _new_session_exercise(self.alice, self.exercise)
        new_prs = _log_and_check(other_performed, weight=Decimal("50"), reps=5)  # 250
        self.assertFalse(any(r.record_type == PRType.SESSION_VOLUME for r in new_prs))

    def test_warmup_sets_never_count_toward_prs(self):
        new_prs = _log_and_check(
            self.performed, weight=Decimal("200"), reps=10, is_warmup=True
        )
        self.assertEqual(new_prs, [])
        self.assertFalse(PersonalRecord.objects.exists())

    def test_failed_sets_never_count_toward_prs(self):
        new_prs = _log_and_check(
            self.performed, weight=Decimal("200"), reps=1, is_failure=True
        )
        self.assertEqual(new_prs, [])
        self.assertFalse(PersonalRecord.objects.exists())

    def test_prs_are_scoped_per_user(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)

        bob_performed = _new_session_exercise(bob, self.exercise)
        new_prs = _log_and_check(bob_performed, weight=Decimal("50"), reps=5)
        # Bob has no history of his own -> everything is a "first PR" for
        # him regardless of Alice's numbers.
        self.assertTrue(any(r.record_type == PRType.MAX_WEIGHT for r in new_prs))

    def test_deleting_the_source_set_preserves_the_pr_record(self):
        _log_and_check(self.performed, weight=Decimal("100"), reps=5)
        record = PersonalRecord.objects.get(
            record_type=PRType.MAX_WEIGHT, exercise=self.exercise
        )
        exercise_set = record.source_set
        exercise_set.delete()

        record.refresh_from_db()
        self.assertIsNone(record.source_set)
        self.assertEqual(record.value, Decimal("100"))


class PRSurvivesProgramChangesTests(TestCase):
    """docs/PR_SYSTEM.md: "A program edit must never erase or rewrite
    previous PRs." PersonalRecord/PRService never reference
    Program/Workout/ExercisePrescription, so this holds by construction —
    asserted directly here."""

    def test_editing_and_deleting_the_program_does_not_affect_stored_prs(self):
        from apps.programs.models import ExercisePrescription, Program, Workout

        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        exercise = Exercise.objects.create(name="Test Deadlift", owner=None)
        program = Program.objects.create(owner=alice, name="Program")
        workout = Workout.objects.create(program=program, name="Day 1")
        ExercisePrescription.objects.create(
            workout=workout, exercise=exercise, target_weight=Decimal("100")
        )
        session = workout_services.start_session(alice, workout=workout)
        performed = session.performed_exercises.get()
        _log_and_check(performed, weight=Decimal("150"), reps=5)

        record = PersonalRecord.objects.get(record_type=PRType.MAX_WEIGHT)
        self.assertEqual(record.value, Decimal("150"))

        program.delete()  # cascades to workout/prescription, not the session

        record.refresh_from_db()
        self.assertEqual(record.value, Decimal("150"))


class ExerciseRecordsViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test OHP", owner=None)
        self.client.login(username="alice", password="s3cret-pass")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(
            reverse("records:exercise-records", args=[self.exercise.pk])
        )
        self.assertEqual(response.status_code, 302)

    def test_shows_current_records_computed_live(self):
        performed = _new_session_exercise(self.alice, self.exercise)
        _log_and_check(performed, weight=Decimal("60"), reps=5)

        response = self.client.get(
            reverse("records:exercise-records", args=[self.exercise.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["records"][PRType.MAX_WEIGHT], Decimal("60"))

    def test_404_for_an_exercise_the_user_cannot_see(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        bobs_exercise = Exercise.objects.create(name="Bob Only", owner=bob)
        response = self.client.get(
            reverse("records:exercise-records", args=[bobs_exercise.pk])
        )
        self.assertEqual(response.status_code, 404)


class SetLoggingNotifiesNewPRsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Test Row", owner=None)
        self.performed = _new_session_exercise(self.alice, self.exercise)
        self.client.login(username="alice", password="s3cret-pass")

    def test_logging_a_set_via_the_view_creates_pr_records(self):
        self.client.post(
            reverse("workouts:set-log", args=[self.performed.pk]),
            {"weight": "100", "reps": "5"},
        )
        self.assertTrue(PersonalRecord.objects.filter(exercise=self.exercise).exists())

    def test_htmx_response_includes_a_pr_banner(self):
        response = self.client.post(
            reverse("workouts:set-log", args=[self.performed.pk]),
            {"weight": "100", "reps": "5"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "New PR")

    def test_new_pr_flash_message_shows_the_converted_weight_with_its_unit(self):
        """Regression: the "New PR" flash message used to interpolate the
        raw stored kg value directly, unconverted and with no unit label
        at all, for every record type — including for an
        imperial-preference user, who'd see a plain number that was
        neither their unit nor labeled as anything."""
        self.alice.unit_system = "imperial"
        self.alice.save()
        response = self.client.post(
            reverse("workouts:set-log", args=[self.performed.pk]),
            {"weight": "225", "reps": "5"},
            follow=True,
        )
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("225.0 lb" in m or "225.00 lb" in m for m in messages), messages)

    def test_htmx_response_renders_the_pr_as_a_top_of_screen_toast(self):
        """New PRs render as an HTMX out-of-band swap into the toast
        container base.html defines once (#pr-toast-container, fixed to
        the top of the screen) — not inline in the exercise card itself."""
        response = self.client.post(
            reverse("workouts:set-log", args=[self.performed.pk]),
            {"weight": "100", "reps": "5"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, 'hx-swap-oob="afterbegin:#pr-toast-container"')

    def test_an_htmx_request_does_not_flash_a_message(self):
        """Regression: messages.success used to fire unconditionally,
        including on every HTMX request — but nothing ever consumes
        django.contrib.messages there (only base.html's full-page `{% if
        messages %}` loop does), so the message sat in the store and
        would resurface, stale, on whatever the user's next *unrelated*
        full page load happened to be. HTMX requests get the toast
        instead (see test above); the message store must stay empty."""
        self.client.post(
            reverse("workouts:set-log", args=[self.performed.pk]),
            {"weight": "100", "reps": "5"},
            HTTP_HX_REQUEST="true",
        )
        response = self.client.get(reverse("workouts:session-list"))
        self.assertNotContains(response, "New PR")
