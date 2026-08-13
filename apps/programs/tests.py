from decimal import Decimal

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

    def test_additional_seeded_templates_exist_and_are_well_formed(self):
        expected_workout_counts = {
            "Arnold Split (6-Day)": 3,
            "Push/Pull/Legs": 3,
            "5x5 Strength (A/B)": 2,
            "Upper/Lower Split (4-Day)": 4,
            "German Volume Training": 2,
        }
        for name, expected_count in expected_workout_counts.items():
            program = Program.objects.get(name=name, owner=None)
            self.assertTrue(program.is_template, name)
            self.assertEqual(program.workouts.count(), expected_count, name)
            for workout in program.workouts.all():
                self.assertGreater(workout.prescriptions.count(), 0, f"{name} / {workout.name}")

    def test_all_seeded_system_templates_are_copyable_end_to_end(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        for template in Program.objects.filter(owner=None, is_template=True):
            copy = services.copy_program(template, owner=alice)
            self.assertEqual(copy.workouts.count(), template.workouts.count())
            copy_prescription_count = ExercisePrescription.objects.filter(
                workout__program=copy
            ).count()
            template_prescription_count = ExercisePrescription.objects.filter(
                workout__program=template
            ).count()
            self.assertEqual(copy_prescription_count, template_prescription_count)


class ProgramContentTranslationTests(TestCase):
    """Built-in template names/descriptions/workout names are content,
    not UI chrome — see apps.programs.i18n_content and
    docs/ARCHITECTURE.md "Internationalization"."""

    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", password="s3cret-pass", language="fi"
        )
        self.client.login(username="alice", password="s3cret-pass")

    def test_template_name_and_workout_name_render_translated(self):
        program = Program.objects.get(name="Push/Pull/Legs", owner=None)
        response = self.client.get(reverse("programs:program-detail", args=[program.pk]))
        self.assertContains(response, "Työntö/Veto/Jalat")
        self.assertNotContains(response, "Push/Pull/Legs")
        self.assertContains(response, "Työntö")  # "Push" workout name

    def test_a_users_own_program_name_is_never_translated(self):
        program = Program.objects.create(owner=self.alice, name="My Weird Program")
        response = self.client.get(reverse("programs:program-detail", args=[program.pk]))
        self.assertContains(response, "My Weird Program")


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


class ExercisePrescriptionUnitConversionTests(TestCase):
    """Regression: ExercisePrescriptionForm always stored target_weight /
    weight_increment as raw kg with a hardcoded "(kg)" label, so an
    imperial-preference user entering a value in pounds had it stored (and
    later re-shown to them) as if it were kilograms."""

    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", password="s3cret-pass", unit_system="imperial"
        )
        self.program = Program.objects.create(owner=self.alice, name="My Program")
        self.workout = Workout.objects.create(program=self.program, name="Day 1")
        self.exercise = Exercise.objects.create(name="Test Move", owner=None)
        self.client.login(username="alice", password="s3cret-pass")

    def test_creating_a_prescription_in_pounds_stores_the_canonical_kg_value(self):
        self.client.post(
            reverse(
                "programs:prescription-create",
                args=[self.program.pk, self.workout.pk],
            ),
            {
                "exercise": self.exercise.pk,
                "order": 0,
                "set_count": 3,
                "min_reps": 8,
                "max_reps": 12,
                "target_weight": "220.46",
                "weight_increment": "5",
                "progression_method": "manual",
            },
        )
        prescription = ExercisePrescription.objects.get(workout=self.workout)
        self.assertEqual(prescription.target_weight, Decimal("100.00"))
        self.assertEqual(prescription.weight_increment, Decimal("2.27"))

    def test_edit_form_shows_pounds_label_and_prefill(self):
        prescription = ExercisePrescription.objects.create(
            workout=self.workout,
            exercise=self.exercise,
            target_weight=Decimal("100"),
        )
        response = self.client.get(
            reverse(
                "programs:prescription-update",
                args=[self.program.pk, self.workout.pk, prescription.pk],
            )
        )
        self.assertContains(response, "Target weight (lb)")
        self.assertContains(response, "220.46")

    def test_rpe_rir_and_1rm_labels_explain_the_abbreviation(self):
        prescription = ExercisePrescription.objects.create(
            workout=self.workout, exercise=self.exercise
        )
        response = self.client.get(
            reverse(
                "programs:prescription-update",
                args=[self.program.pk, self.workout.pk, prescription.pk],
            )
        )
        self.assertContains(
            response, '<abbr tabindex="0" title="Rate of Perceived Exertion">RPE</abbr>'
        )
        self.assertContains(response, '<abbr tabindex="0" title="Reps In Reserve">RIR</abbr>')
        self.assertContains(response, '<abbr tabindex="0" title="One-Rep Max">1RM</abbr>')


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

    def test_list_annotates_workout_count_correctly(self):
        """Regression coverage for Phase 11's query-count fix: the list
        page annotates workout_count in the queryset instead of calling
        `program.workouts.count` per row in the template."""
        program = Program.objects.create(owner=self.alice, name="Annotated Program")
        Workout.objects.create(program=program, name="Day 1")
        Workout.objects.create(program=program, name="Day 2")
        response = self.client.get(reverse("programs:program-list"))
        found = next(p for p in response.context["programs"] if p.pk == program.pk)
        self.assertEqual(found.workout_count, 2)

    def test_can_view_system_template(self):
        template = Program.objects.get(name="Full Body A/B/C", owner=None)
        response = self.client.get(
            reverse("programs:program-detail", args=[template.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_edit"])

    def test_own_template_shows_a_copy_button_and_template_tag(self):
        my_template = Program.objects.create(
            owner=self.alice, name="My Own Template", is_template=True
        )
        response = self.client.get(
            reverse("programs:program-detail", args=[my_template.pk])
        )
        self.assertContains(response, "My template")
        self.assertContains(response, "Copy to a new program")

    def test_regular_own_program_shows_neither_template_tag_nor_copy_button(self):
        program = Program.objects.create(owner=self.alice, name="Regular")
        response = self.client.get(reverse("programs:program-detail", args=[program.pk]))
        self.assertNotContains(response, "My template")
        self.assertNotContains(response, "Copy to a new program")


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

    def test_create_program_defaults_to_not_a_template(self):
        self.client.post(reverse("programs:program-create"), {"name": "Regular Program"})
        program = Program.objects.get(name="Regular Program")
        self.assertFalse(program.is_template)

    def test_can_mark_my_own_program_as_a_personal_template(self):
        response = self.client.post(
            reverse("programs:program-create"),
            {"name": "My Template", "is_template": "on"},
        )
        program = Program.objects.get(name="My Template")
        self.assertTrue(program.is_template)
        self.assertRedirects(
            response, reverse("programs:program-detail", args=[program.pk])
        )

    def test_editing_a_program_can_toggle_is_template(self):
        program = Program.objects.create(owner=self.alice, name="Toggle Me")
        self.client.post(
            reverse("programs:program-update", args=[program.pk]),
            {"name": "Toggle Me", "is_template": "on"},
        )
        program.refresh_from_db()
        self.assertTrue(program.is_template)

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

    def test_program_detail_page_offers_a_direct_delete_button_per_workout(self):
        """Regression: workout_delete already worked, but was only
        reachable via Edit workout -> Delete workout, a click deeper
        than necessary and easy to miss."""
        program = Program.objects.create(owner=self.alice, name="Original")
        workout = Workout.objects.create(program=program, name="Day 1")
        response = self.client.get(reverse("programs:program-detail", args=[program.pk]))
        self.assertContains(
            response, reverse("programs:workout-delete", args=[program.pk, workout.pk])
        )

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

    def test_cannot_copy_another_users_private_program_even_if_flagged_as_a_template(self):
        """is_template is a UI affordance for the owner, not a visibility
        grant — a personal template stays exactly as private as any
        other program belonging to someone else."""
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        bob_template = Program.objects.create(
            owner=bob, name="Bob's Template", is_template=True
        )
        response = self.client.post(
            reverse("programs:program-copy", args=[bob_template.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_can_copy_my_own_template_into_a_fresh_program(self):
        my_template = Program.objects.create(
            owner=self.alice, name="My PPL Template", is_template=True
        )
        Workout.objects.create(program=my_template, name="Push")
        response = self.client.post(
            reverse("programs:program-copy", args=[my_template.pk])
        )
        new_program = (
            Program.objects.filter(owner=self.alice, name="My PPL Template")
            .exclude(pk=my_template.pk)
            .get()
        )
        self.assertRedirects(
            response, reverse("programs:program-detail", args=[new_program.pk])
        )
        self.assertFalse(new_program.is_template)
        self.assertEqual(new_program.workouts.count(), 1)
        # The original template is untouched.
        self.assertTrue(Program.objects.get(pk=my_template.pk).is_template)
