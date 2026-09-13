from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


def exercise_image_upload_to(instance, filename):
    return f"exercise_images/{instance.exercise_id}/{filename}"


class MuscleGroup(models.Model):
    """A trainable muscle group (Chest, Back, ...).

    System-seeded lookup data (see migration 0002) — not user-created.
    """

    name = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Equipment(models.Model):
    """Equipment an exercise may require (Barbell, Dumbbell, ...).

    System-seeded lookup data (see migration 0002) — not user-created.
    """

    name = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = _("equipment")

    def __str__(self):
        return self.name


class MovementType(models.TextChoices):
    COMPOUND = "compound", _("Compound")
    ISOLATION = "isolation", _("Isolation")


class WeightInputMode(models.TextChoices):
    """How a set's logged weight should be interpreted.

    TOTAL: the weight as logged is the full load (barbell, machine stack,
    bodyweight + added weight, ...).
    PER_HAND: the weight as logged is per dumbbell/hand; total load for
    volume math is double the logged value. Kept as an explicit per-exercise
    choice (rather than a global setting) so logging and progression/PR math
    agree on the same convention — see docs/DOMAIN_MODEL.md.
    """

    TOTAL = "total", _("Total load")
    PER_HAND = "per_hand", _("Per hand / dumbbell")


class Exercise(TimeStampedModel):
    """A movement that can be prescribed and logged.

    `owner` is null for system exercises (available to everyone) and set to
    a user for that user's custom exercises. Exercises are never hard
    deleted — `active=False` is used instead, so past workout history that
    references an exercise keeps rendering correctly after it's retired
    (see docs/DOMAIN_MODEL.md).
    """

    name = models.CharField(max_length=100, verbose_name=_("name"))
    description = models.TextField(blank=True, verbose_name=_("description"))
    # Separate from `description` above: description is a short blurb
    # of what the movement is/targets (shown at the top of the detail
    # page), while this is the step-by-step "how to actually perform
    # it" text, shown together with `ExerciseImage`s in the detail
    # page's own "Instructions" section — text and images both
    # answering the same "how do I do this" question, just in whichever
    # form (written steps, a photo, both) the exercise actually needs.
    instructions = models.TextField(blank=True, verbose_name=_("instructions"))
    # Same reasoning as `ExerciseImage.attribution` — set only for the
    # seeded library's own instructions sourced from wger.de's own
    # CC-BY-SA-licensed exercise database (see migration 0010's own
    # docstring); blank for a user's own custom exercise, which needs
    # no credit line for text they wrote themselves.
    instructions_attribution = models.CharField(
        max_length=200, blank=True, verbose_name=_("instructions attribution")
    )
    primary_muscle_groups = models.ManyToManyField(
        MuscleGroup,
        related_name="primary_exercises",
        blank=True,
        verbose_name=_("primary muscle groups"),
    )
    secondary_muscle_groups = models.ManyToManyField(
        MuscleGroup,
        related_name="secondary_exercises",
        blank=True,
        verbose_name=_("secondary muscle groups"),
    )
    equipment = models.ForeignKey(
        Equipment,
        related_name="exercises",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("equipment"),
    )
    movement_type = models.CharField(
        max_length=20,
        choices=MovementType.choices,
        default=MovementType.COMPOUND,
        verbose_name=_("movement type"),
    )
    weight_input_mode = models.CharField(
        max_length=20,
        choices=WeightInputMode.choices,
        default=WeightInputMode.TOTAL,
        verbose_name=_("weight logging"),
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="custom_exercises",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="Null for built-in system exercises.",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # System exercise names must be unique among system exercises;
            # a user's custom exercise names must be unique per-user. Two
            # different users may each have their own "Bench Press v2".
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(owner__isnull=True),
                name="unique_system_exercise_name",
            ),
            models.UniqueConstraint(
                fields=["owner", "name"],
                condition=models.Q(owner__isnull=False),
                name="unique_user_exercise_name",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_custom(self):
        return self.owner_id is not None


class ExerciseImage(TimeStampedModel):
    """One instructional image for an `Exercise` — a photo or diagram
    showing how to perform it, optionally captioned with a step/cue
    (e.g. "Bar just below the collarbone, elbows tucked ~45°"). An
    exercise can hold several, shown in `order`, so a multi-step
    movement can be illustrated start-to-finish rather than with one
    single photo.

    Management mirrors `Exercise` itself: system exercises' images are
    added/reordered from the admin only (see migration 0006's own
    seeding of the built-in library's images from `apps.exercises.
    seed_data.exercise_images`), while a user with their own custom
    exercise manages its images from the exercise detail page
    (apps.exercises.views.exercise_image_create/exercise_image_delete).

    `ExerciseImageSettings.max_images_per_exercise` caps how many an
    exercise may hold at once — enforced in `clean()` rather than a DB
    constraint, since the cap is an admin-adjustable setting, not a
    fixed schema-level number. Django's `ModelForm._post_clean()` runs
    `full_clean()` automatically, so both the admin's inline formset
    and `apps.exercises.forms.ExerciseImageForm` enforce this without
    each needing its own duplicate check.
    """

    exercise = models.ForeignKey(
        Exercise, related_name="images", on_delete=models.CASCADE
    )
    image = models.ImageField(upload_to=exercise_image_upload_to, verbose_name=_("image"))
    caption = models.CharField(max_length=200, blank=True, verbose_name=_("caption"))
    # Free-text credit for images sourced from an externally-licensed
    # library (e.g. the seeded system library's own images — see
    # apps.exercises.seed_data.exercise_images/manifest.json) rather
    # than uploaded by the exercise's own owner. A Creative Commons
    # attribution license requires this to stay attached to the image
    # wherever it's redistributed; blank for a user's own photo of
    # their own custom exercise, which needs no credit line.
    attribution = models.CharField(max_length=200, blank=True, verbose_name=_("attribution"))
    order = models.PositiveSmallIntegerField(default=0, verbose_name=_("order"))

    class Meta:
        ordering = ["order", "created_at"]

    def __str__(self):
        return f"{self.exercise.name} image #{self.order}"

    def clean(self):
        super().clean()
        from . import services

        if self.exercise_id and services.image_limit_exceeded(
            self.exercise, exclude_pk=self.pk
        ):
            max_images = ExerciseImageSettings.load().max_images_per_exercise
            raise ValidationError(
                _(
                    "This exercise already has the maximum of %(max)d image(s) "
                    "allowed. Remove one before adding another."
                )
                % {"max": max_images}
            )


class ExerciseImageSettings(models.Model):
    """Singleton row (always pk=1 — see `load()`) holding the one knob
    for how many images (apps.exercises.models.ExerciseImage) a single
    exercise may hold at once, adjustable from /admin/ — same pattern
    as apps.core.models.BackupSettings/FeedbackSettings/SeoSettings.
    A per-exercise cap, rather than no limit at all, keeps a single
    exercise's gallery from growing unbounded (disk usage, and a long
    scroll on the exercise detail page) while still allowing enough
    images to show a multi-step movement start-to-finish.
    """

    max_images_per_exercise = models.PositiveSmallIntegerField(
        default=5,
        validators=[MinValueValidator(1)],
        help_text=_(
            "How many images a single exercise may hold at once. Applies to "
            "every exercise, system and custom alike."
        ),
    )

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — deleting it would just silently recreate defaults on next load()

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Exercise image settings"
