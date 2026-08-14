from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class UnitSystem(models.TextChoices):
    METRIC = "metric", _("Metric (kg, km)")
    IMPERIAL = "imperial", _("Imperial (lb, mi)")


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
    show_achievements = models.BooleanField(
        default=True,
        help_text="A privacy setting, not a display one: the dashboard's "
        "achievements carousel (longest streak, workout count, PRs, "
        "total volume — see apps.analytics.achievements) is shared "
        "across every user on this instance, so this controls whether "
        "*this* user's own achievements are included in what everyone "
        "sees, not whether they personally see the carousel at all.",
    )
    # Applied by apps.accounts.middleware.UserLanguageMiddleware — a
    # distinct concern from unit_system/timezone above (see
    # config.settings.base's LANGUAGES comment). Defaults to
    # settings.LANGUAGE_CODE's base language ("en-us" -> "en") rather
    # than an empty string, so a freshly created user always has an
    # explicit, valid choice rather than silently falling back to
    # whatever LocaleMiddleware would otherwise guess.
    language = models.CharField(
        max_length=10, choices=settings.LANGUAGES, default=settings.LANGUAGE_CODE.split("-")[0]
    )

    def __str__(self):
        return self.username
