from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class ActivityType(models.Model):
    """A kind of non-gym activity (Running, Yoga, ...).

    System-seeded defaults (see migration 0002) plus user-created custom
    types — same ownership/uniqueness/soft-delete pattern as
    apps.exercises.Exercise and apps.measurements.MeasurementType: never
    hard-deleted, so a user's past Activity entries keep rendering
    correctly after a custom type is retired.
    """

    name = models.CharField(max_length=50)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="custom_activity_types",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="Null for built-in system activity types.",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(owner__isnull=True),
                name="unique_system_activity_type_name",
            ),
            models.UniqueConstraint(
                fields=["owner", "name"],
                condition=models.Q(owner__isnull=False),
                name="unique_user_activity_type_name",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_custom(self):
        return self.owner_id is not None


class Activity(TimeStampedModel):
    """One manually logged non-gym activity — see docs/DOMAIN_MODEL.md.

    `date`/`start_time` are kept separate (rather than one combined
    timestamp, unlike apps.workouts.WorkoutSession.started_at): a user
    logging "went for a run today" shouldn't have to also state a precise
    clock time they may not remember or care about, so `start_time` is
    optional. `distance` is canonical meters (docs/ARCHITECTURE.md) and
    optional — plenty of activity types (yoga, sports) have none.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="activities", on_delete=models.CASCADE
    )
    activity_type = models.ForeignKey(
        ActivityType, related_name="activities", on_delete=models.PROTECT
    )
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    duration = models.DurationField()
    distance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    calories = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-start_time"]
        verbose_name_plural = "activities"

    def __str__(self):
        return f"{self.activity_type.name} ({self.date})"
