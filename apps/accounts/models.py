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

    def __str__(self):
        return self.username
