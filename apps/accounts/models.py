from django.contrib.auth.models import AbstractUser
from django.db import models


class UnitSystem(models.TextChoices):
    METRIC = "metric", "Metric (kg, km)"
    IMPERIAL = "imperial", "Imperial (lb, mi)"


class User(AbstractUser):
    """Custom user model.

    Required from the start (Django can't swap the user model after the
    first migration). Carries the per-user display preferences referenced
    throughout docs/DOMAIN_MODEL.md — internal data always stays in
    canonical units (see apps.core.units); these fields only drive display.
    """

    unit_system = models.CharField(
        max_length=10, choices=UnitSystem.choices, default=UnitSystem.METRIC
    )
    timezone = models.CharField(max_length=64, default="UTC")
    height = models.DecimalField(
        # Same precision as apps.measurements.BodyMeasurement.value for a
        # length reading (0.1mm) — a cm/inch round-trip through
        # apps.core.units never loses precision at this scale.
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Canonical meters — see apps.core.units. Optional; only "
        "used to compute BMI alongside a logged body weight.",
    )
    show_bmi = models.BooleanField(
        default=True,
        help_text="Whether the dashboard's BMI card is shown at all — "
        "independent of whether height/weight exist to compute it, so a "
        "user who'd rather not see the figure can turn it off outright.",
    )

    def __str__(self):
        return self.username
