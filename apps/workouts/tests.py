from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.exercises.models import Exercise
from apps.programs.models import ExercisePrescription, Program, Workout

from . import services
from .models import PerformedExercise, WorkoutSession, WorkoutSessionStatus

User = get_user_model()


def _make_workout_with_prescription(owner, **prescription_overrides):
    program = Program.objects.create(owner=owner, name="Test Program")
    workout = Workout.objects.create(program=program, name="Day 1")
    exercise = Exercise.objects.create(name="Test Squat", owner=None)
    defaults = dict(
        workout=workout,
        exercise=exercise,
        order=0,
        set_count=3,
        min_reps=5,
        max_reps=5,
        target_weight=Decimal("100.00"),
        progression_method="linear",
        weight_increment=Decimal("2.5"),
    )
    defaults.update(prescription_overrides)
    prescription = ExercisePrescription.objects.create(**defaults)
    return workout, prescription


class StartSessionSnapshotTests(TestCase):
    """The core historical-trustworthiness guarantee: see CLAUDE.md."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.workout, self.prescription = _make_workout_with_prescription(self.alice)

    def test_starting_a_session_snapshots_prescription_values(self):
        session = services.start_session(self.alice, workout=self.workout)
        performed = session.performed_exercises.get()
        self.assertEqual(performed.exercise, self.prescription.exercise)
        self.assertEqual(performed.set_count, 3)
        self.assertEqual(performed.min_reps, 5)
        self.assertEqual(performed.max_reps, 5)
        self.assertEqual(performed.target_weight, Decimal("100.00"))
        self.assertEqual(performed.progression_method, "linear")
        self.assertEqual(performed.prescription, self.prescription)

    def test_editing_the_prescription_after_session_start_does_not_change_the_snapshot(self):
        session = services.start_session(self.alice, workout=self.workout)
        performed = session.performed_exercises.get()

        self.prescription.target_weight = Decimal("999.00")
        self.prescription.set_count = 10
        self.prescription.save()

        performed.refresh_from_db()
        self.assertEqual(performed.target_weight, Decimal("100.00"))
        self.assertEqual(performed.set_count, 3)

    def test_deleting_the_prescription_after_session_start_preserves_the_snapshot(self):
        session = services.start_session(self.alice, workout=self.workout)
        performed = session.performed_exercises.get()

        self.prescription.delete()

        performed.refresh_from_db()
        self.assertIsNone(performed.prescription)
        self.assertEqual(performed.target_weight, Decimal("100.00"))
        self.assertEqual(performed.set_count, 3)

    def test_freeform_session_has_no_performed_exercises(self):
        session = services.start_session(self.alice, workout=None)
        self.assertEqual(session.performed_exercises.count(), 0)
        self.assertIsNone(session.workout)
        self.assertIsNone(session.program)


class SessionLifecycleTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.session = services.start_session(self.alice, workout=None)

    def test_new_session_is_in_progress(self):
        self.assertEqual(self.session.status, WorkoutSessionStatus.IN_PROGRESS)
        self.assertTrue(self.session.is_in_progress)

    def test_complete_session_sets_status_and_ended_at(self):
        services.complete_session(self.session)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, WorkoutSessionStatus.COMPLETED)
        self.assertIsNotNone(self.session.ended_at)

    def test_abandon_session_sets_status_and_ended_at(self):
        services.abandon_session(self.session)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, WorkoutSessionStatus.ABANDONED)
        self.assertIsNotNone(self.session.ended_at)


class SetLoggingServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.workout, self.prescription = _make_workout_with_prescription(self.alice)
        self.session = services.start_session(self.alice, workout=self.workout)
        self.performed = self.session.performed_exercises.get()

    def test_log_set_auto_numbers_sequentially(self):
        first = services.log_set(self.performed, weight=Decimal("100"), reps=5)
        second = services.log_set(self.performed, weight=Decimal("100"), reps=5)
        self.assertEqual(first.set_number, 1)
        self.assertEqual(second.set_number, 2)

    def test_default_set_values_falls_back_to_target_when_no_sets_logged(self):
        defaults = services.default_set_values(self.performed)
        self.assertEqual(defaults["weight"], Decimal("100.00"))
        self.assertEqual(defaults["reps"], 5)

    def test_default_set_values_repeats_the_last_logged_set(self):
        services.log_set(self.performed, weight=Decimal("102.5"), reps=4)
        defaults = services.default_set_values(self.performed)
        self.assertEqual(defaults["weight"], Decimal("102.5"))
        self.assertEqual(defaults["reps"], 4)


class SessionViewPermissionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.bob_session = services.start_session(self.bob, workout=None)
        self.client.login(username="alice", password="s3cret-pass")

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("workouts:session-list"))
        self.assertEqual(response.status_code, 302)

    def test_cannot_view_another_users_session(self):
        response = self.client.get(
            reverse("workouts:session-detail", args=[self.bob_session.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_complete_another_users_session(self):
        response = self.client.post(
            reverse("workouts:session-complete", args=[self.bob_session.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.bob_session.refresh_from_db()
        self.assertTrue(self.bob_session.is_in_progress)

    def test_cannot_start_a_session_from_another_users_private_workout(self):
        program = Program.objects.create(owner=self.bob, name="Bob Program")
        workout = Workout.objects.create(program=program, name="Bob Day 1")
        response = self.client.post(reverse("workouts:session-start", args=[workout.pk]))
        self.assertEqual(response.status_code, 404)

    def test_can_start_a_session_from_a_system_template_workout(self):
        template = Program.objects.get(name="Full Body A/B/C", owner=None)
        workout = template.workouts.first()
        response = self.client.post(reverse("workouts:session-start", args=[workout.pk]))
        session = WorkoutSession.objects.get(user=self.alice, workout=workout)
        self.assertRedirects(
            response, reverse("workouts:session-detail", args=[session.pk])
        )
        self.assertGreater(session.performed_exercises.count(), 0)

    def test_cannot_log_a_set_on_another_users_performed_exercise(self):
        performed = PerformedExercise.objects.create(
            session=self.bob_session, exercise=Exercise.objects.create(name="X", owner=None)
        )
        response = self.client.post(
            reverse("workouts:set-log", args=[performed.pk]),
            {"weight": "100", "reps": "5"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(performed.sets.count(), 0)


class SetLoggingFlowTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.workout, self.prescription = _make_workout_with_prescription(self.alice)
        self.session = services.start_session(self.alice, workout=self.workout)
        self.performed = self.session.performed_exercises.get()
        self.client.login(username="alice", password="s3cret-pass")

    def test_logging_a_set_via_the_view(self):
        response = self.client.post(
            reverse("workouts:set-log", args=[self.performed.pk]),
            {"weight": "100.00", "reps": "5"},
        )
        self.assertRedirects(
            response, reverse("workouts:session-detail", args=[self.session.pk])
        )
        self.assertEqual(self.performed.sets.count(), 1)
        logged = self.performed.sets.get()
        self.assertEqual(logged.set_number, 1)
        self.assertEqual(logged.weight, Decimal("100.00"))

    def test_cannot_log_a_set_once_session_is_completed(self):
        services.complete_session(self.session)
        response = self.client.post(
            reverse("workouts:set-log", args=[self.performed.pk]),
            {"weight": "100.00", "reps": "5"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.performed.sets.count(), 0)

    def test_editing_a_set_updates_it(self):
        exercise_set = services.log_set(self.performed, weight=Decimal("100"), reps=5)
        response = self.client.post(
            reverse("workouts:set-edit", args=[exercise_set.pk]),
            {"weight": "102.5", "reps": "4"},
        )
        self.assertEqual(response.status_code, 302)
        exercise_set.refresh_from_db()
        self.assertEqual(exercise_set.weight, Decimal("102.5"))
        self.assertEqual(exercise_set.reps, 4)

    def test_deleting_a_set_removes_it(self):
        exercise_set = services.log_set(self.performed, weight=Decimal("100"), reps=5)
        self.client.post(reverse("workouts:set-delete", args=[exercise_set.pk]))
        self.assertEqual(self.performed.sets.count(), 0)

    def test_completed_and_abandoned_sessions_remain_visible_in_history(self):
        services.complete_session(self.session)
        other_session = services.start_session(self.alice, workout=None)
        services.abandon_session(other_session)

        response = self.client.get(reverse("workouts:session-list"))
        session_ids = {s.pk for s in response.context["sessions"]}
        self.assertIn(self.session.pk, session_ids)
        self.assertIn(other_session.pk, session_ids)


class SmartSuggestionIntegrationTests(TestCase):
    """Phase 7: the progression engine's suggestion reaches the logging
    form as a pre-filled, freely-overridable default — never a black box,
    never forced (docs/SMART_SUGGESTIONS.md)."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.workout, self.prescription = _make_workout_with_prescription(self.alice)
        self.session = services.start_session(self.alice, workout=self.workout)
        self.performed = self.session.performed_exercises.get()
        self.client.login(username="alice", password="s3cret-pass")

    def test_detail_page_shows_a_suggestion_before_any_set_is_logged(self):
        response = self.client.get(reverse("workouts:session-detail", args=[self.session.pk]))
        performed = response.context["session"].performed_exercises.all()[0]
        self.assertIsNotNone(performed.suggestion)
        # No history yet -> insufficient data, falls back to the
        # prescribed target (docs/SMART_SUGGESTIONS.md "Insufficient
        # history").
        self.assertEqual(performed.suggestion.suggested_weight, Decimal("100.00"))
        self.assertContains(response, "Suggested: 100.00 kg")

    def test_the_suggested_weight_pre_fills_the_set_log_form(self):
        response = self.client.get(reverse("workouts:session-detail", args=[self.session.pk]))
        performed = response.context["session"].performed_exercises.all()[0]
        self.assertEqual(performed.set_form.initial["weight"], Decimal("100.00"))

    def test_user_can_log_a_different_weight_than_the_one_suggested(self):
        """"Never prevent a user from entering a different value" —
        exercised end-to-end: the suggestion pre-fills 100kg, but nothing
        stops logging something else entirely."""
        response = self.client.post(
            reverse("workouts:set-log", args=[self.performed.pk]),
            {"weight": "37.5", "reps": "12"},
        )
        self.assertEqual(response.status_code, 302)
        logged = self.performed.sets.get()
        self.assertEqual(logged.weight, Decimal("37.5"))
        self.assertEqual(logged.reps, 12)

    def test_suggestion_disappears_once_a_set_has_been_logged(self):
        services.log_set(self.performed, weight=Decimal("100"), reps=5)
        response = self.client.get(reverse("workouts:session-detail", args=[self.session.pk]))
        performed = response.context["session"].performed_exercises.all()[0]
        self.assertIsNone(performed.suggestion)

    def test_freeform_exercises_with_no_prescription_get_no_suggestion(self):
        freeform_session = services.start_session(self.alice, workout=None)
        exercise = Exercise.objects.create(name="Ad Hoc Move", owner=None)
        performed = services.add_performed_exercise(freeform_session, exercise)

        response = self.client.get(
            reverse("workouts:session-detail", args=[freeform_session.pk])
        )
        performed = response.context["session"].performed_exercises.all()[0]
        self.assertIsNone(performed.suggestion)

    def test_completed_sessions_show_no_suggestion(self):
        services.complete_session(self.session)
        response = self.client.get(reverse("workouts:session-detail", args=[self.session.pk]))
        performed = response.context["session"].performed_exercises.all()[0]
        self.assertIsNone(performed.suggestion)
