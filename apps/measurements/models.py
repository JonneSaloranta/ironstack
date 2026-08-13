from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class UnitKind(models.TextChoices):
    """How a measurement type's canonical `value` should be interpreted
    and converted for display — see apps.measurements.units.

    WEIGHT: canonical kilograms (kg/lb display).
    LENGTH: canonical meters (cm/inches display) — circumferences.
    PERCENTAGE: dimensionless 0-100, no unit conversion at all.
    """

    WEIGHT = "weight", "Weight"
    LENGTH = "length", "Length/circumference"
    PERCENTAGE = "percentage", "Percentage"


class MeasurementType(models.Model):
    """A trackable kind of body measurement (Weight, Waist, ...).

    System-seeded defaults (see migration 0002) cover
    docs/DOMAIN_MODEL.md's supported list; `owner` is set for a user's own
    custom measurement type, same ownership/visibility pattern as
    apps.exercises.Exercise. Never hard-deleted — `active=False` instead,
    so historical BodyMeasurement entries keep rendering correctly after a
    user retires a custom type they no longer track.
    """

    name = models.CharField(max_length=50)
    unit_kind = models.CharField(max_length=20, choices=UnitKind.choices)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="custom_measurement_types",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="Null for built-in system measurement types.",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(owner__isnull=True),
                name="unique_system_measurement_type_name",
            ),
            models.UniqueConstraint(
                fields=["owner", "name"],
                condition=models.Q(owner__isnull=False),
                name="unique_user_measurement_type_name",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_custom(self):
        return self.owner_id is not None


class BodyMeasurement(TimeStampedModel):
    """One time-stamped reading of a measurement type.

    `value` is always canonical for `measurement_type.unit_kind` (kg,
    meters, or a raw 0-100 percentage) — see apps.measurements.units for
    conversion to/from the user's display unit preference.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="body_measurements", on_delete=models.CASCADE
    )
    measurement_type = models.ForeignKey(
        MeasurementType, related_name="measurements", on_delete=models.PROTECT
    )
    value = models.DecimalField(max_digits=8, decimal_places=4)
    # Indexed: history/chart pages (Meta.ordering below) order by this.
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"{self.measurement_type.name}: {self.value} ({self.recorded_at:%Y-%m-%d})"
