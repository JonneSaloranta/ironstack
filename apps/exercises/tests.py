import io

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import Equipment, Exercise, ExerciseImage, ExerciseImageSettings, MuscleGroup
from .services import visible_to

User = get_user_model()


def _tiny_image(name="test.png"):
    """A minimal real PNG, not just arbitrary bytes — Django's
    ImageField (via Pillow) validates that an uploaded file actually
    decodes as an image, so a fake `b"not-an-image"` upload would fail
    validation for the wrong reason in every test below."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


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

    def test_search_also_matches_the_translated_name(self):
        # Regression: exercise names are stored in English ("Barbell
        # Row") but displayed translated (`|translate_content` —
        # "Kulmasoutu tangolla" for a Finnish user, its real msgstr in
        # locale/fi). Searching in the language actually shown on
        # screen used to find nothing at all, since the old plain
        # `name__icontains` only ever matched the stored English.
        #
        # `translation.override()` alone doesn't reach the view here —
        # apps.core.middleware overrides LocaleMiddleware's own guess
        # with the logged-in user's own `language` field partway
        # through the request, same as a real browser request would
        # go through, so that's what actually needs setting.
        self.alice.language = "fi"
        self.alice.save()
        response = self.client.get(reverse("exercises:exercise-list"), {"q": "kulmasoutu"})
        names = {e.name for e in response.context["exercises"]}
        self.assertIn("Barbell Row", names)

    def test_search_still_matches_english_regardless_of_active_language(self):
        self.alice.language = "fi"
        self.alice.save()
        response = self.client.get(reverse("exercises:exercise-list"), {"q": "Barbell Row"})
        names = {e.name for e in response.context["exercises"]}
        self.assertIn("Barbell Row", names)

    def test_muscle_group_filter(self):
        from apps.exercises.models import MuscleGroup

        # Names distinct from the seeded library's own muscle groups
        # (MuscleGroup.name is unique) — "Chest"/"Legs" already exist
        # from the seed migration.
        group_a = MuscleGroup.objects.create(name="Test Group Alpha")
        group_b = MuscleGroup.objects.create(name="Test Group Beta")
        bench = Exercise.objects.create(name="Zzyzx Bench Press", owner=None)
        bench.primary_muscle_groups.add(group_a)
        squat = Exercise.objects.create(name="Zzyzx Squat", owner=None)
        squat.primary_muscle_groups.add(group_b)

        response = self.client.get(
            reverse("exercises:exercise-list"), {"muscle_group": group_a.pk}
        )
        names = {e.name for e in response.context["exercises"]}
        self.assertIn("Zzyzx Bench Press", names)
        self.assertNotIn("Zzyzx Squat", names)

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

    def test_updating_my_own_exercise(self):
        """ExerciseUpdateView's actual success path had no test —
        ExercisePermissionTests only ever checks the 404-for-another-
        user's-exercise case (found via `coverage report`)."""
        exercise = Exercise.objects.create(
            name="Old Name", owner=self.alice, movement_type="isolation",
            weight_input_mode="total",
        )
        response = self.client.post(
            reverse("exercises:exercise-update", args=[exercise.pk]),
            {"name": "New Name", "movement_type": "isolation", "weight_input_mode": "total"},
        )
        self.assertRedirects(
            response, reverse("exercises:exercise-detail", args=[exercise.pk])
        )
        exercise.refresh_from_db()
        self.assertEqual(exercise.name, "New Name")


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


class ExerciseInstructionsTests(TestCase):
    """`Exercise.instructions` — the written step-by-step counterpart to
    `ExerciseImage` (see that field's own docstring for how the two
    relate), editable the same way `description` already is."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_create_saves_instructions(self):
        response = self.client.post(
            reverse("exercises:exercise-create"),
            {
                "name": "My New Exercise",
                "movement_type": "isolation",
                "weight_input_mode": "total",
                "instructions": "1. Set up.\n2. Perform the rep.",
            },
        )
        exercise = Exercise.objects.get(name="My New Exercise")
        self.assertRedirects(
            response, reverse("exercises:exercise-detail", args=[exercise.pk])
        )
        self.assertEqual(exercise.instructions, "1. Set up.\n2. Perform the rep.")

    def test_detail_page_renders_instructions_with_line_breaks(self):
        exercise = Exercise.objects.create(
            name="My Move", owner=self.alice, instructions="Step one.\nStep two."
        )
        response = self.client.get(reverse("exercises:exercise-detail", args=[exercise.pk]))
        self.assertContains(response, "Step one.<br>Step two.")

    def test_detail_page_renders_instructions_attribution_when_present(self):
        exercise = Exercise.objects.create(
            name="My Move",
            owner=self.alice,
            instructions="Step one.",
            instructions_attribution="Someone — CC-BY-SA, via wger.de",
        )
        response = self.client.get(reverse("exercises:exercise-detail", args=[exercise.pk]))
        self.assertContains(response, "Someone — CC-BY-SA, via wger.de")

    def test_detail_page_omits_the_instructions_card_for_a_bare_system_exercise(self):
        # No instructions, no images, and not the viewer's own exercise
        # to manage — nothing left to show, so the whole card (not just
        # its now-empty contents) is skipped.
        exercise = Exercise.objects.create(name="Bare System Move", owner=None)
        response = self.client.get(reverse("exercises:exercise-detail", args=[exercise.pk]))
        self.assertNotContains(response, ">Instructions<")


class ExerciseImageModelTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Alice Move", owner=self.alice)
        ExerciseImageSettings.load()  # ensure default singleton row (max=5)

    def test_clean_allows_image_under_the_limit(self):
        image = ExerciseImage(exercise=self.exercise, image=_tiny_image())
        image.full_clean()  # must not raise

    def test_clean_rejects_image_at_the_limit(self):
        ExerciseImageSettings.objects.update(max_images_per_exercise=1)
        ExerciseImage.objects.create(exercise=self.exercise, image=_tiny_image("first.png"))
        image = ExerciseImage(exercise=self.exercise, image=_tiny_image("second.png"))
        with self.assertRaises(ValidationError):
            image.full_clean()

    def test_clean_excludes_the_instance_being_edited_from_its_own_count(self):
        # Regression: re-saving an already-existing image (e.g. editing
        # its caption) must not count itself against the limit.
        ExerciseImageSettings.objects.update(max_images_per_exercise=1)
        image = ExerciseImage.objects.create(exercise=self.exercise, image=_tiny_image())
        image.caption = "Updated caption"
        image.full_clean()  # must not raise


class ExerciseImageSettingsTests(TestCase):
    def test_load_creates_a_singleton_row_with_the_default(self):
        settings_obj = ExerciseImageSettings.load()
        self.assertEqual(settings_obj.pk, 1)
        self.assertEqual(settings_obj.max_images_per_exercise, 5)

    def test_save_always_pins_to_pk_one(self):
        settings_obj = ExerciseImageSettings(max_images_per_exercise=3)
        settings_obj.save()
        self.assertEqual(settings_obj.pk, 1)
        self.assertEqual(ExerciseImageSettings.objects.count(), 1)


class ExerciseImageViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.exercise = Exercise.objects.create(name="Alice Move", owner=self.alice)
        self.client.login(username="alice", password="s3cret-pass")
        # Ensures the singleton row already exists, so a later test's
        # own `ExerciseImageSettings.objects.update(...)` (a no-op
        # against zero rows) actually hits one instead of silently
        # leaving `max_images_per_exercise` at its unadjusted default.
        ExerciseImageSettings.load()

    def test_create_adds_an_image_to_own_exercise(self):
        response = self.client.post(
            reverse("exercises:exercise-image-create", args=[self.exercise.pk]),
            {"image": _tiny_image(), "caption": "Step 1"},
        )
        self.assertRedirects(
            response, reverse("exercises:exercise-detail", args=[self.exercise.pk])
        )
        self.assertEqual(self.exercise.images.count(), 1)
        self.assertEqual(self.exercise.images.first().caption, "Step 1")

    def test_cannot_add_an_image_to_another_users_exercise(self):
        bob_exercise = Exercise.objects.create(name="Bob Move", owner=self.bob)
        response = self.client.post(
            reverse("exercises:exercise-image-create", args=[bob_exercise.pk]),
            {"image": _tiny_image()},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(bob_exercise.images.count(), 0)

    def test_get_is_not_allowed(self):
        response = self.client.get(
            reverse("exercises:exercise-image-create", args=[self.exercise.pk])
        )
        self.assertEqual(response.status_code, 405)

    def test_upload_past_the_limit_is_rejected_without_creating_a_row(self):
        ExerciseImageSettings.objects.update(max_images_per_exercise=1)
        ExerciseImage.objects.create(exercise=self.exercise, image=_tiny_image("first.png"))
        response = self.client.post(
            reverse("exercises:exercise-image-create", args=[self.exercise.pk]),
            {"image": _tiny_image("second.png")},
        )
        self.assertRedirects(
            response, reverse("exercises:exercise-detail", args=[self.exercise.pk])
        )
        self.assertEqual(self.exercise.images.count(), 1)

    def test_delete_removes_own_image(self):
        image = ExerciseImage.objects.create(exercise=self.exercise, image=_tiny_image())
        response = self.client.post(reverse("exercises:exercise-image-delete", args=[image.pk]))
        self.assertRedirects(
            response, reverse("exercises:exercise-detail", args=[self.exercise.pk])
        )
        self.assertFalse(ExerciseImage.objects.filter(pk=image.pk).exists())

    def test_cannot_delete_another_users_image(self):
        bob_exercise = Exercise.objects.create(name="Bob Move", owner=self.bob)
        image = ExerciseImage.objects.create(exercise=bob_exercise, image=_tiny_image())
        response = self.client.post(reverse("exercises:exercise-image-delete", args=[image.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ExerciseImage.objects.filter(pk=image.pk).exists())


class SeedExerciseImagesTests(TestCase):
    """Regression coverage for migration 0007_seed_exercise_images —
    see that migration's own docstring for why "Side Plank" is the one
    seeded exercise deliberately left without one."""

    def test_most_seeded_system_exercises_have_an_image(self):
        seeded_with_images = Exercise.objects.filter(owner=None, images__isnull=False).distinct()
        self.assertGreaterEqual(seeded_with_images.count(), 27)

    def test_side_plank_has_no_good_source_match_and_ships_without_one(self):
        side_plank = Exercise.objects.get(name="Side Plank", owner=None)
        self.assertEqual(side_plank.images.count(), 0)

    def test_seeded_images_carry_attribution(self):
        squat = Exercise.objects.get(name="Barbell Back Squat", owner=None)
        image = squat.images.first()
        self.assertIsNotNone(image)
        self.assertTrue(image.attribution)


class SeedExerciseInstructionsTests(TestCase):
    """Regression coverage for migration 0010_seed_exercise_instructions
    — see that migration's own docstring for why "Side Plank" is left
    without instructions too, and why a few of the 27 carry no
    `instructions_attribution` despite the rest being wger-sourced."""

    def test_most_seeded_system_exercises_have_instructions(self):
        seeded_with_instructions = Exercise.objects.filter(owner=None).exclude(instructions="")
        self.assertGreaterEqual(seeded_with_instructions.count(), 27)

    def test_side_plank_has_no_good_source_match_and_ships_without_instructions(self):
        side_plank = Exercise.objects.get(name="Side Plank", owner=None)
        self.assertEqual(side_plank.instructions, "")

    def test_a_wger_sourced_exercise_carries_attribution(self):
        squat = Exercise.objects.get(name="Barbell Back Squat", owner=None)
        self.assertTrue(squat.instructions)
        self.assertTrue(squat.instructions_attribution)

    def test_a_rewritten_exercise_carries_no_attribution(self):
        # Regression: Face Pull's own wger source described a dumbbell
        # variant, wrong equipment for how this project seeds it
        # (Cable) — rewritten from scratch, so it must not carry a
        # credit line for text that isn't actually wger's.
        face_pull = Exercise.objects.get(name="Face Pull", owner=None)
        self.assertTrue(face_pull.instructions)
        self.assertEqual(face_pull.instructions_attribution, "")
