from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import Equipment, Exercise, MuscleGroup
from .services import visible_to

User = get_user_model()


class ExerciseLibrarySeedTests(TestCase):
    def test_seed_migration_creates_muscle_groups_equipment_and_exercises(self):
        self.assertEqual(MuscleGroup.objects.count(), 14)
        self.assertGreaterEqual(Equipment.objects.count(), 5)
        self.assertGreater(Exercise.objects.filter(owner=None).count(), 0)

    def test_second_seed_migration_adds_more_exercises(self):
        for name in [
            "Romanian Deadlift",
            "Front Squat",
            "Incline Barbell Bench Press",
            "Hip Thrust",
            "Seated Cable Row",
            "Face Pull",
            "Lateral Raise",
            "Hammer Curl",
            "Skull Crusher",
            "Ab Wheel Rollout",
        ]:
            self.assertTrue(
                Exercise.objects.filter(name=name, owner=None).exists(), name
            )

    def test_third_seed_migration_adds_traps_lats_and_obliques(self):
        """Regression: the original 11 seeded muscle groups left out
        Traps, Lats, and Obliques — three groups mainstream fitness
        apps usually list on their own rather than folding into a
        broader neighbor (Back/Abs)."""
        for name in ["Traps", "Lats", "Obliques"]:
            self.assertTrue(MuscleGroup.objects.filter(name=name).exists(), name)

    def test_third_seed_migration_adds_one_exercise_per_new_muscle_group(self):
        """Each new muscle group ships with at least one exercise tagged
        to it, so it isn't an empty option in the muscle-group filter —
        see the migration's own docstring for why existing exercises
        aren't retagged instead."""
        cases = [
            ("Barbell Shrug", "Traps"),
            ("Straight-Arm Pulldown", "Lats"),
            ("Side Plank", "Obliques"),
        ]
        for exercise_name, muscle_group_name in cases:
            exercise = Exercise.objects.get(name=exercise_name, owner=None)
            self.assertTrue(
                exercise.primary_muscle_groups.filter(name=muscle_group_name).exists(),
                exercise_name,
            )


class ExerciseContentTranslationTests(TestCase):
    """Seeded exercise/muscle-group/equipment *names* are content, not UI
    chrome — the stored value stays canonical English (matched by
    get_or_create/uniqueness elsewhere), but the display goes through
    gettext too, via apps.exercises.i18n_content's extraction catalog —
    see docs/ARCHITECTURE.md "Internationalization"."""

    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", password="s3cret-pass", language="fi"
        )
        self.client.login(username="alice", password="s3cret-pass")

    def test_exercise_name_renders_translated_for_a_non_english_user(self):
        exercise = Exercise.objects.get(name="Barbell Back Squat", owner=None)
        response = self.client.get(reverse("exercises:exercise-detail", args=[exercise.pk]))
        self.assertContains(response, "Takakyykky tangolla")
        self.assertNotContains(response, "Barbell Back Squat")

    def test_a_users_own_custom_exercise_name_is_never_translated(self):
        """gettext only ever matches strings actually present in the
        catalog — a custom name a user typed themselves was never
        extracted into it, so it always renders exactly as typed,
        regardless of UI language."""
        exercise = Exercise.objects.create(name="My Weird Custom Move", owner=self.alice)
        response = self.client.get(reverse("exercises:exercise-detail", args=[exercise.pk]))
        self.assertContains(response, "My Weird Custom Move")


class ExerciseModelTests(TestCase):
    def test_system_exercise_names_must_be_unique(self):
        Exercise.objects.create(name="Bench Press", owner=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Exercise.objects.create(name="Bench Press", owner=None)

    def test_two_users_can_each_have_a_custom_exercise_with_the_same_name(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        Exercise.objects.create(name="Cable Fly Variant", owner=alice)
        # Should not raise: unique_user_exercise_name is scoped per-owner.
        Exercise.objects.create(name="Cable Fly Variant", owner=bob)

    def test_same_user_cannot_duplicate_a_custom_exercise_name(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        Exercise.objects.create(name="My Curl Variant", owner=alice)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Exercise.objects.create(name="My Curl Variant", owner=alice)

    def test_is_custom_reflects_ownership(self):
        alice = User.objects.create_user(username="alice", password="s3cret-pass")
        system_exercise = Exercise.objects.create(name="System Move", owner=None)
        custom_exercise = Exercise.objects.create(name="My Move", owner=alice)
        self.assertFalse(system_exercise.is_custom)
        self.assertTrue(custom_exercise.is_custom)


class VisibleToServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.system_exercise = Exercise.objects.create(name="System Move", owner=None)
        self.alice_exercise = Exercise.objects.create(name="Alice Move", owner=self.alice)
        self.bob_exercise = Exercise.objects.create(name="Bob Move", owner=self.bob)
        self.inactive_alice_exercise = Exercise.objects.create(
            name="Retired Move", owner=self.alice, active=False
        )

    def test_visible_to_includes_system_and_own_custom_exercises(self):
        qs = visible_to(self.alice)
        self.assertIn(self.system_exercise, qs)
        self.assertIn(self.alice_exercise, qs)
        self.assertNotIn(self.bob_exercise, qs)

    def test_visible_to_excludes_inactive_by_default(self):
        qs = visible_to(self.alice)
        self.assertNotIn(self.inactive_alice_exercise, qs)

    def test_visible_to_can_include_inactive(self):
        qs = visible_to(self.alice, include_inactive=True)
        self.assertIn(self.inactive_alice_exercise, qs)


class ExerciseListViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("exercises:exercise-list"))
        self.assertEqual(response.status_code, 302)

    def test_the_plain_function_view_also_requires_login(self):
        # Regression: exercise_deactivate was missing @login_required,
        # so an anonymous request crashed with a 500 instead of
        # redirecting to login — same bug class as apps.nutrition's
        # own equivalent fix.
        exercise = Exercise.objects.create(name="Alice Move", owner=self.alice)
        self.client.logout()
        response = self.client.post(reverse("exercises:exercise-deactivate", args=[exercise.pk]))
        self.assertEqual(response.status_code, 302)

    def test_list_shows_system_and_own_exercises_not_other_users(self):
        Exercise.objects.create(name="System Move", owner=None)
        Exercise.objects.create(name="Alice Move", owner=self.alice)
        Exercise.objects.create(name="Bob Move", owner=self.bob)
        response = self.client.get(reverse("exercises:exercise-list"))
        names = {e.name for e in response.context["exercises"]}
        self.assertIn("System Move", names)
        self.assertIn("Alice Move", names)
        self.assertNotIn("Bob Move", names)

    def test_search_filters_by_name(self):
        Exercise.objects.create(name="Zzyzx Curl", owner=None)
        response = self.client.get(reverse("exercises:exercise-list"), {"q": "zzyzx"})
        names = {e.name for e in response.context["exercises"]}
        self.assertEqual(names, {"Zzyzx Curl"})

    def test_htmx_request_returns_partial_template(self):
        response = self.client.get(
            reverse("exercises:exercise-list"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(
            response.templates[0].name, "exercises/_exercise_list_results.html"
        )


class ExerciseCreateViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_create_sets_owner_to_current_user(self):
        response = self.client.post(
            reverse("exercises:exercise-create"),
            {
                "name": "My New Exercise",
                "movement_type": "isolation",
                "weight_input_mode": "total",
            },
        )
        exercise = Exercise.objects.get(name="My New Exercise")
        self.assertEqual(exercise.owner, self.alice)
        self.assertRedirects(
            response, reverse("exercises:exercise-detail", args=[exercise.pk])
        )


class ExercisePermissionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.bob_exercise = Exercise.objects.create(name="Bob Move", owner=self.bob)
        self.client.login(username="alice", password="s3cret-pass")

    def test_cannot_edit_another_users_custom_exercise(self):
        response = self.client.get(
            reverse("exercises:exercise-update", args=[self.bob_exercise.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_deactivate_another_users_custom_exercise(self):
        response = self.client.post(
            reverse("exercises:exercise-deactivate", args=[self.bob_exercise.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.bob_exercise.refresh_from_db()
        self.assertTrue(self.bob_exercise.active)

    def test_cannot_view_another_users_custom_exercise_detail(self):
        # Custom exercises are user-owned data, same as programs/workouts —
        # visible_to() scopes the detail view to system + own exercises.
        response = self.client.get(
            reverse("exercises:exercise-detail", args=[self.bob_exercise.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_owner_can_deactivate_own_exercise(self):
        self.client.logout()
        self.client.login(username="bob", password="s3cret-pass")
        response = self.client.post(
            reverse("exercises:exercise-deactivate", args=[self.bob_exercise.pk])
        )
        self.assertRedirects(response, reverse("exercises:exercise-list"))
        self.bob_exercise.refresh_from_db()
        self.assertFalse(self.bob_exercise.active)
