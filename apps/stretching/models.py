from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

MIN_HOLD_SECONDS = 5
MAX_HOLD_SECONDS = 600
MAX_REST_SECONDS = 300
MAX_SETS = 10


class StretchKind(models.TextChoices):
    STATIC = "static", _("Static")
    DYNAMIC = "dynamic", _("Dynamic")


class Stretch(models.Model):
    """One stretch in the library (Standing Quad Stretch, Cat-Cow, ...).

    System-seeded defaults (see migration 0002) plus user-created custom
    stretches — the same ownership/uniqueness/soft-delete pattern as
    apps.activities.ActivityType: never hard-deleted, so routines and
    past sessions keep rendering after a custom stretch is retired.
    `muscle_groups` reuses apps.exercises.MuscleGroup so a finished
    workout's trained muscles map straight onto matching stretches
    (services.suggest_cooldown) without a separate lookup table.
    """

    name = models.CharField(max_length=100, verbose_name=_("name"))
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="custom_stretches",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="Null for built-in system stretches.",
    )
    active = models.BooleanField(default=True)
    kind = models.CharField(
        max_length=10,
        choices=StretchKind.choices,
        default=StretchKind.STATIC,
        verbose_name=_("type"),
    )
    instructions = models.TextField(blank=True, verbose_name=_("instructions"))
    muscle_groups = models.ManyToManyField(
        "exercises.MuscleGroup",
        related_name="stretches",
        blank=True,
        verbose_name=_("muscle groups"),
    )
    default_hold_seconds = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(MIN_HOLD_SECONDS), MaxValueValidator(MAX_HOLD_SECONDS)],
        verbose_name=_("default hold (seconds)"),
    )
    per_side = models.BooleanField(
        default=False,
        verbose_name=_("one side at a time"),
        help_text=_("Held once for the left side and once for the right."),
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = _("stretches")
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(owner__isnull=True),
                name="unique_system_stretch_name",
            ),
            models.UniqueConstraint(
                fields=["owner", "name"],
                condition=models.Q(owner__isnull=False),
                name="unique_user_stretch_name",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_custom(self):
        return self.owner_id is not None


class StretchRoutine(TimeStampedModel):
    """An editable, reusable list of stretches — the stretching
    equivalent of apps.programs.Workout. Editing a routine never touches
    past StretchSessions: those snapshot everything they need at start
    time (services.start_session)."""

    name = models.CharField(max_length=100, verbose_name=_("name"))
    description = models.TextField(blank=True, verbose_name=_("description"))
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="stretch_routines",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="Null for built-in system routines.",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(owner__isnull=True),
                name="unique_system_stretch_routine_name",
            ),
            models.UniqueConstraint(
                fields=["owner", "name"],
                condition=models.Q(owner__isnull=False),
                name="unique_user_stretch_routine_name",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_custom(self):
        return self.owner_id is not None


class RoutineItem(models.Model):
    routine = models.ForeignKey(StretchRoutine, related_name="items", on_delete=models.CASCADE)
    stretch = models.ForeignKey(Stretch, related_name="routine_items", on_delete=models.PROTECT)
    order = models.PositiveIntegerField(default=0)
    hold_seconds = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(MIN_HOLD_SECONDS), MaxValueValidator(MAX_HOLD_SECONDS)],
        verbose_name=_("hold (seconds)"),
    )
    sets = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_SETS)],
        verbose_name=_("sets"),
    )
    rest_seconds = models.PositiveIntegerField(
        default=10,
        validators=[MaxValueValidator(MAX_REST_SECONDS)],
        verbose_name=_("rest between holds (seconds)"),
    )

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.routine} — {self.stretch}"


class StretchSessionStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")
    ABANDONED = "abandoned", _("Abandoned")


class StretchSession(TimeStampedModel):
    """One stretching session — guided (snapshotted from a routine or a
    post-workout suggestion) or quick-logged after the fact with just a
    duration. `routine` is an informational backlink only; `name` is
    the snapshot history renders from."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="stretch_sessions", on_delete=models.CASCADE
    )
    routine = models.ForeignKey(
        StretchRoutine, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    after_workout = models.ForeignKey(
        "workouts.WorkoutSession",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cooldown_stretch_sessions",
    )
    name = models.CharField(max_length=100, blank=True, verbose_name=_("name"))
    status = models.CharField(
        max_length=20,
        choices=StretchSessionStatus.choices,
        default=StretchSessionStatus.IN_PROGRESS,
    )
    # Indexed: history/chart/calendar all filter and order by this.
    date = models.DateField(default=timezone.localdate, db_index=True, verbose_name=_("date"))
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Wall-clock time from start to finish for a guided session, or the
    # user's own figure for a quick log — see services.complete_session.
    duration = models.DurationField(null=True, blank=True, verbose_name=_("duration"))
    notes = models.TextField(blank=True, verbose_name=_("notes"))

    class Meta:
        ordering = ["-date", "-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="in_progress"),
                name="one_in_progress_stretch_session_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.name or _('Stretching')} ({self.date})"


class PerformedStretchStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    DONE = "done", _("Done")
    SKIPPED = "skipped", _("Skipped")


class PerformedStretch(models.Model):
    """One stretch within a session, holding the snapshot of what was
    planned when the session started — name, hold, sets, rest, sides.
    `stretch` is kept only as a backlink (for its instructions while
    the session is being played); history never re-reads it."""

    session = models.ForeignKey(
        StretchSession, related_name="performed_stretches", on_delete=models.CASCADE
    )
    stretch = models.ForeignKey(
        Stretch, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    order = models.PositiveIntegerField(default=0)
    stretch_name = models.CharField(max_length=100)
    per_side = models.BooleanField(default=False)
    target_hold_seconds = models.PositiveIntegerField()
    sets = models.PositiveSmallIntegerField(default=1)
    rest_seconds = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=10,
        choices=PerformedStretchStatus.choices,
        default=PerformedStretchStatus.PENDING,
    )
    actual_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.stretch_name
