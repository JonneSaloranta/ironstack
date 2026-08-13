from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


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
        verbose_name_plural = "equipment"

    def __str__(self):
        return self.name


class MovementType(models.TextChoices):
    COMPOUND = "compound", "Compound"
    ISOLATION = "isolation", "Isolation"


class WeightInputMode(models.TextChoices):
    """How a set's logged weight should be interpreted.

    TOTAL: the weight as logged is the full load (barbell, machine stack,
    bodyweight + added weight, ...).
    PER_HAND: the weight as logged is per dumbbell/hand; total load for
    volume math is double the logged value. Kept as an explicit per-exercise
    choice (rather than a global setting) so logging and progression/PR math
    agree on the same convention — see docs/DOMAIN_MODEL.md.
    """

    TOTAL = "total", "Total load"
    PER_HAND = "per_hand", "Per hand / dumbbell"


class Exercise(TimeStampedModel):
    """A movement that can be prescribed and logged.

    `owner` is null for system exercises (available to everyone) and set to
    a user for that user's custom exercises. Exercises are never hard
    deleted — `active=False` is used instead, so past workout history that
    references an exercise keeps rendering correctly after it's retired
    (see docs/DOMAIN_MODEL.md).
    """

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    primary_muscle_groups = models.ManyToManyField(
        MuscleGroup, related_name="primary_exercises", blank=True
    )
    secondary_muscle_groups = models.ManyToManyField(
        MuscleGroup, related_name="secondary_exercises", blank=True
    )
    equipment = models.ForeignKey(
        Equipment,
        related_name="exercises",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    movement_type = models.CharField(
        max_length=20, choices=MovementType.choices, default=MovementType.COMPOUND
    )
    weight_input_mode = models.CharField(
        max_length=20,
        choices=WeightInputMode.choices,
        default=WeightInputMode.TOTAL,
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
