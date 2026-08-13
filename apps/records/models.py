from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel
from apps.exercises.models import Exercise
from apps.workouts.models import ExerciseSet


class PRType(models.TextChoices):
    MAX_WEIGHT = "max_weight", "Max weight"
    REP_PR = "rep_pr", "Rep PR"
    REP_SPECIFIC_PR = "rep_specific_pr", "Rep-specific PR"
    ESTIMATED_1RM = "estimated_1rm", "Estimated 1RM"
    SET_VOLUME = "set_volume", "Set volume"
    SESSION_VOLUME = "session_volume", "Session volume"


class PersonalRecord(TimeStampedModel):
    """An immutable log entry: "this set was a new personal record".

    PRs are always derived from actual `ExerciseSet` history (see
    docs/PR_SYSTEM.md — "PR detection must be based on actual historical
    workout data, not the current program") — this model never references
    Program/Workout/ExercisePrescription, so program edits can't affect it.

    `value` is the headline metric, in the unit natural to `record_type`:
    - max_weight, rep_specific_pr, estimated_1rm, set_volume,
      session_volume: kilograms
    - rep_pr: rep count

    `weight`/`reps` are always the source set's raw weight/reps, kept as
    display context regardless of type (see docs/PR_SYSTEM.md's
    notification example: "Bench Press — 100 kg × 5"). `rep_count` is only
    populated for rep_specific_pr — the N in "NRM" (e.g. 5 for "5RM").

    Rows are never rewritten to reflect a later edit/deletion of the
    source set — `source_set` merely goes null (see `on_delete`) while the
    achievement itself remains on record, same as any other historical
    fact in this application. The one exception is `session_volume`: since
    it's a running total across a session rather than a single set's raw
    number, later sets in the *same* session update this row in place
    instead of creating a new one each time (see
    apps.records.services._upsert_session_volume_record) — otherwise every
    single set in a good session would fire its own "new PR" notification.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="personal_records", on_delete=models.CASCADE
    )
    exercise = models.ForeignKey(
        Exercise, related_name="personal_records", on_delete=models.PROTECT
    )
    record_type = models.CharField(max_length=20, choices=PRType.choices)
    rep_count = models.PositiveIntegerField(
        null=True, blank=True, help_text="The N in 'NRM' — only set for rep_specific_pr."
    )
    value = models.DecimalField(max_digits=8, decimal_places=2)
    weight = models.DecimalField(max_digits=6, decimal_places=2)
    reps = models.PositiveIntegerField()
    source_set = models.ForeignKey(
        ExerciseSet,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="prs_achieved",
    )
    achieved_at = models.DateTimeField()

    class Meta:
        ordering = ["-achieved_at"]

    def __str__(self):
        return f"{self.exercise.name} {self.get_record_type_display()}: {self.value}"
