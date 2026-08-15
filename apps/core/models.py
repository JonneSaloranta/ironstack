from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    """Abstract base adding created/updated timestamps.

    Reused across apps instead of redefining these fields on every model.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def _default_backup_hour():
    # A plain lambda can't be used as a field default — Django's
    # migration writer needs an importable, named reference to
    # serialize into the migration file. Only used to *seed* the
    # singleton row the first time it's created (get_or_create in
    # load() below); settings.BACKUP_HOUR/the DJANGO_BACKUP_HOUR env
    # var has no further effect after that — BackupSettings.hour is
    # the one adjustable knob apps.core.management.commands.
    # backup_scheduler actually reads, on every loop iteration, so a
    # change here takes effect without restarting that container.
    return settings.BACKUP_HOUR


class BackupSettings(models.Model):
    """Singleton row (always pk=1 — see `load()`) holding the backup-
    scheduler knobs adjustable from Profile → Administration → Backups
    without a redeploy — the same pattern `apps.api.models.ApiSettings`
    already uses for its own admin-tunable knobs."""

    enabled = models.BooleanField(
        default=True,
        help_text=_(
            "Turns the backup-scheduler service's daily backup off without "
            "needing to stop or remove that service."
        ),
    )
    hour = models.PositiveSmallIntegerField(
        default=_default_backup_hour,
        validators=[MaxValueValidator(23)],
        help_text=_("What time (UTC) the daily automatic backup runs at."),
    )
    retention_count = models.PositiveIntegerField(
        default=14,
        help_text=_(
            "How many backups to keep — older ones are deleted automatically "
            "after each new one is created. 0 keeps every backup forever."
        ),
    )

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — deleting it would just silently recreate defaults on next load()

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Backup settings"
