from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.exercises.models import Exercise

from . import services
from .models import ExercisePrescription, Program, Workout

User = get_user_model()


class ProgramTemplateSeedTests(TestCase):
    def test_seed_migration_creates_a_built_in_template(self):
        program = Program.objects.get(name="Full Body A/B/C", owner=None)
        self.assertTrue(program.is_template)
        self.assertEqual(program.workouts.count(), 3)
        first_workout = program.workouts.order_by("order").first()
        self.assertGreater(first_workout.prescriptions.count(), 0)


class ExercisePrescriptionModelTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.program = Program.objects.create(owner=self.alice, name="My Program")
        self.workout = Workout.objects.create(program=self.program, name="Day 1")
        self.exercise = Exercise.objects.create(name="Test Move", owner=None)

    def test_min_reps_cannot_exceed_max_reps(self):
        prescription = ExercisePrescription(
            workout=self.workout, exercise=self.exercise, min_reps=12, max_reps=8
        )
        with self.assertRaises(ValidationError):
            prescription.full_clean()


class ProgramVisibilityServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.alice_program = Program.objects.create(owner=self.alice, name="Alice Program")
        self.bob_program = Program.objects.create(owner=self.bob, name="Bob Program")

    def test_visible_to_includes_own_and_system_templates_not_other_users(self):
        qs = services.visible_to(self.alice)
        self.assertIn(self.alice_program, qs)
        self.assertNotIn(self.bob_program, qs)
        self.assertTrue(
            qs.filter(name="Full Body A/B/C", owner__isnull=True).exists()
        )

    def test_editable_by_excludes_system_templates(self):
        qs = services.editable_by(self.alice)
        self.assertIn(self.alice_program, qs)
        self.assertFalse(qs.filter(owner__isnull=True).exists())


class CopyProgramServiceTests(TestCase):
    def test_copy_program_deep_copies_workouts_and_prescriptions(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        template = Program.objects.get(name="Full Body A/B/C", owner=None)

        copy = services.copy_program(template, owner=alice)

        self.assertEqual(copy.owner, alice)
        self.assertFalse(copy.is_template)
        self.assertEqual(copy.workouts.count(), template.workouts.count())
        template_prescription_count = ExercisePrescription.objects.filter(
            workout__program=template
        ).count()
        copy_prescription_count = ExercisePrescription.objects.filter(
            workout__program=copy
        ).count()
        self.assertEqual(copy_prescription_count, template_prescription_count)

    def test_editing_the_copy_does_not_affect_the_source_template(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        template = Program.objects.get(name="Full Body A/B/C", owner=None)
        original_workout_count = template.workouts.count()

        copy = services.copy_program(template, owner=alice)
        copy.workouts.first().delete()

        template.refresh_from_db()
        self.assertEqual(template.workouts.count(), original_workout_count)


class ProgramViewPermissionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.bob_program = Program.objects.create(owner=self.bob, name="Bob Program")
        self.client.login(username="alice", password="s3cret-pass")

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("programs:program-list"))
        self.assertEqual(response.status_code, 302)

    def test_cannot_view_another_users_program(self):
        response = self.client.get(
            reverse("programs:program-detail", args=[self.bob_program.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_edit_another_users_program(self):
        response = self.client.get(
            reverse("programs:program-update", args=[self.bob_program.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_add_workout_to_another_users_program(self):
        response = self.client.post(
            reverse("programs:workout-create", args=[self.bob_program.pk]),
            {"name": "Sneaky Workout", "order": 0},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.bob_program.workouts.filter(name="Sneaky Workout").exists())

    def test_can_view_system_template(self):
        template = Program.objects.get(name="Full Body A/B/C", owner=None)
        response = self.client.get(
            reverse("programs:program-detail", args=[template.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_edit"])


class ProgramCreateEditFlowTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_create_program_sets_owner(self):
        response = self.client.post(
            reverse("programs:program-create"), {"name": "My New Program"}
        )
        program = Program.objects.get(name="My New Program")
        self.assertEqual(program.owner, self.alice)
        self.assertRedirects(
            response, reverse("programs:program-detail", args=[program.pk])
        )

    def test_editing_a_program_bumps_version(self):
        program = Program.objects.create(owner=self.alice, name="Original")
        self.assertEqual(program.version, 1)
        self.client.post(
            reverse("programs:program-update", args=[program.pk]),
            {"name": "Renamed"},
        )
        program.refresh_from_db()
        self.assertEqual(program.name, "Renamed")
        self.assertEqual(program.version, 2)

    def test_adding_a_workout_bumps_program_version(self):
        program = Program.objects.create(owner=self.alice, name="Original")
        self.client.post(
            reverse("programs:workout-create", args=[program.pk]),
            {"name": "Workout A", "order": 0},
        )
        program.refresh_from_db()
        self.assertEqual(program.version, 2)
        self.assertTrue(program.workouts.filter(name="Workout A").exists())

    def test_adding_a_prescription_limits_exercise_choices_to_visible_exercises(self):
        program = Program.objects.create(owner=self.alice, name="Original")
        workout = Workout.objects.create(program=program, name="Day 1")
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        bobs_exercise = Exercise.objects.create(name="Bob Only Move", owner=bob)

        response = self.client.post(
            reverse(
                "programs:prescription-create", args=[program.pk, workout.pk]
            ),
            {
                "exercise": bobs_exercise.pk,
                "order": 0,
                "set_count": 3,
                "min_reps": 8,
                "max_reps": 12,
                "progression_method": "manual",
            },
        )
        self.assertEqual(response.status_code, 200)  # form re-rendered with error
        self.assertFalse(
            ExercisePrescription.objects.filter(exercise=bobs_exercise).exists()
        )


class ProgramCopyViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_copy_template_creates_owned_program_and_redirects(self):
        template = Program.objects.get(name="Full Body A/B/C", owner=None)
        response = self.client.post(reverse("programs:program-copy", args=[template.pk]))
        new_program = Program.objects.get(owner=self.alice, name="Full Body A/B/C")
        self.assertRedirects(
            response, reverse("programs:program-detail", args=[new_program.pk])
        )
        self.assertEqual(new_program.workouts.count(), template.workouts.count())

    def test_cannot_copy_another_users_private_program(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        bob_program = Program.objects.create(owner=bob, name="Bob Program")
        response = self.client.post(
            reverse("programs:program-copy", args=[bob_program.pk])
        )
        self.assertEqual(response.status_code, 404)
