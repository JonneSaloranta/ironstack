from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.exercises.models import Exercise
from apps.programs.models import ExercisePrescription, Program, ProgressionMethod, Workout


class WorkoutSessionStatus(models.TextChoices):
    PLANNED = "planned", _("Planned")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")
    ABANDONED = "abandoned", _("Abandoned")


class WorkoutSession(TimeStampedModel):
    """An actual attempt at a workout — the historical record of a gym visit.

    `program`/`workout` are informational links only, per
    docs/ARCHITECTURE.md "snapshot-on-start": history is never computed by
    dereferencing them, so editing or deleting the program later can't
    change what this session says happened. A session isn't required to
    come from a program at all — a freeform session (both null) lets a
    user log an ad-hoc workout.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="workout_sessions", on_delete=models.CASCADE
    )
    program = models.ForeignKey(
        Program, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    workout = models.ForeignKey(
        Workout, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    status = models.CharField(
        max_length=20,
        choices=WorkoutSessionStatus.choices,
        default=WorkoutSessionStatus.IN_PROGRESS,
    )
    # Indexed: every history/analytics view orders and range-filters by
    # this (Meta.ordering below, apps.analytics.dateranges filters).
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        label = self.workout.name if self.workout else "Freeform workout"
        return f"{label} — {self.started_at:%Y-%m-%d}"

    @property
    def is_in_progress(self):
        return self.status == WorkoutSessionStatus.IN_PROGRESS


class PerformedExercise(TimeStampedModel):
    """One exercise within a session, holding the snapshot of what was
    prescribed at the moment the session was created (see
    docs/DOMAIN_MODEL.md "PerformedExercise"). `prescription` is kept only
    as an informational backlink — it may later be edited, deactivated, or
    deleted without touching any field snapshotted here.
    """

    session = models.ForeignKey(
        WorkoutSession, related_name="performed_exercises", on_delete=models.CASCADE
    )
    exercise = models.ForeignKey(Exercise, on_delete=models.PROTECT)
    prescription = models.ForeignKey(
        ExercisePrescription,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="performed_exercises",
    )
    order = models.PositiveIntegerField(default=0)

    # Snapshotted plan — copied at session-start time, never re-read from
    # `prescription` afterward. All null/blank for exercises added ad hoc
    # during a freeform (or deviated-from-plan) session.
    set_count = models.PositiveIntegerField(null=True, blank=True)
    min_reps = models.PositiveIntegerField(null=True, blank=True)
    max_reps = models.PositiveIntegerField(null=True, blank=True)
    target_weight = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    target_rpe = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    target_rir = models.PositiveIntegerField(null=True, blank=True)
    progression_method = models.CharField(
        max_length=20, choices=ProgressionMethod.choices, blank=True
    )
    weight_increment = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["session_id", "order", "id"]

    def __str__(self):
        return f"{self.exercise.name} ({self.session_id})"


class ExerciseSet(TimeStampedModel):
    """One actual performed set — the historical unit of truth. `created_at`
    (from TimeStampedModel) is the DB audit timestamp; `performed_at` is
    the domain timestamp of when the set happened and, unlike most
    historical data here, may legitimately be corrected later (e.g.
    logging a set a few minutes after actually doing it).
    """

    performed_exercise = models.ForeignKey(
        PerformedExercise, related_name="sets", on_delete=models.CASCADE
    )
    set_number = models.PositiveIntegerField()
    weight = models.DecimalField(max_digits=6, decimal_places=2)
    reps = models.PositiveIntegerField()
    target_reps = models.PositiveIntegerField(null=True, blank=True)
    rpe = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    rir = models.PositiveIntegerField(null=True, blank=True)
    is_failure = models.BooleanField(default=False)
    is_warmup = models.BooleanField(
        default=False,
        help_text="Excluded from PR, progression, and analytics calculations by default.",
    )
    notes = models.TextField(blank=True)
    performed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["performed_exercise_id", "set_number"]

    def __str__(self):
        return f"Set {self.set_number}: {self.weight}kg × {self.reps}"
