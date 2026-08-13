from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimeStampedModel
from apps.exercises.models import Exercise


class Program(TimeStampedModel):
    """A reusable training program: a named collection of workouts.

    `owner` is null for built-in system templates (seeded, read-only,
    available to everyone to copy). A user's own programs always have
    `owner` set.

    `version` is a display-only counter — see docs/ARCHITECTURE.md
    "Historical integrity mechanism: snapshot-on-start". It is not a
    row-versioning system: editing a program never rewrites history,
    because workout sessions (from Phase 4 onward) copy the prescription
    values they need at creation time instead of referencing this program
    live. Bumping it is just so the UI can tell a user "this program
    changed since you started using it".
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="programs",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="Null for built-in system templates.",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_template = models.BooleanField(
        default=False,
        help_text="Available to be copied rather than run directly.",
    )
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_system_template(self):
        return self.owner_id is None

    def bump_version(self):
        """Record that the program's structure changed (display-only)."""
        self.version += 1
        self.save(update_fields=["version", "updated_at"])


class Weekday(models.IntegerChoices):
    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class Workout(TimeStampedModel):
    """A planned workout inside a program (e.g. "Workout A").

    `scheduled_weekday` is optional — a program can be run as a fixed
    weekly schedule or as an unscheduled rotation the user works through
    in `order` (see docs/PRODUCT_REQUIREMENTS.md).
    """

    program = models.ForeignKey(Program, related_name="workouts", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)
    scheduled_weekday = models.IntegerField(
        choices=Weekday.choices, null=True, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["program_id", "order", "id"]

    def __str__(self):
        return f"{self.program.name} — {self.name}"


class ProgressionMethod(models.TextChoices):
    """See docs/PROGRESSION.md. The algorithms themselves belong to
    apps.progression (Phase 6) — this enum just records, per prescription,
    which one applies; apps.progression will interpret it."""

    DOUBLE_PROGRESSION = "double_progression", "Double progression"
    LINEAR = "linear", "Linear"
    PERCENTAGE_BASED = "percentage_based", "Percentage based"
    RPE_RIR = "rpe_rir", "RPE/RIR based"
    REP_RANGE = "rep_range", "Rep range"
    MAINTENANCE = "maintenance", "Maintenance"
    MANUAL = "manual", "Manual"


class ExercisePrescription(TimeStampedModel):
    """What a workout expects for one exercise: sets, rep range, targets.

    Values here are the *plan*. Once workout logging exists (Phase 4),
    starting a session snapshots these onto the session's own records —
    editing a prescription afterward never changes already-performed
    history (see docs/ARCHITECTURE.md).
    """

    workout = models.ForeignKey(
        Workout, related_name="prescriptions", on_delete=models.CASCADE
    )
    exercise = models.ForeignKey(
        Exercise, related_name="prescriptions", on_delete=models.PROTECT
    )
    order = models.PositiveIntegerField(default=0)
    set_count = models.PositiveIntegerField(default=3)
    min_reps = models.PositiveIntegerField(default=8)
    max_reps = models.PositiveIntegerField(default=12)
    target_weight = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Canonical kg. What a workout session snapshots as its "
        "starting target — set manually for now; a future progression "
        "engine (Phase 6) may update it between cycles.",
    )
    target_rpe = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True
    )
    target_rir = models.PositiveIntegerField(null=True, blank=True)
    progression_method = models.CharField(
        max_length=20,
        choices=ProgressionMethod.choices,
        default=ProgressionMethod.MANUAL,
    )
    weight_increment = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, default=Decimal("2.5")
    )
    percentage_target = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["workout_id", "order", "id"]

    def __str__(self):
        return f"{self.workout.name} — {self.exercise.name}"

    def clean(self):
        if self.min_reps and self.max_reps and self.min_reps > self.max_reps:
            raise ValidationError({"min_reps": "Minimum reps cannot exceed maximum reps."})
