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


class FeedbackSettings(models.Model):
    """Singleton row (always pk=1 — see `load()`) holding the single
    knob for apps.core.views_feedback.FeedbackCreateView: whether
    submitting new feedback is currently open at all. Same pattern as
    BackupSettings/apps.api.models.ApiSettings above. Turning this off
    only closes new submissions (gated in the view itself, not just
    hidden on the profile page — see FeedbackCreateView.dispatch) —
    it never touches feedback already on file, which stays visible to
    staff either way."""

    enabled = models.BooleanField(
        default=True,
        help_text=_(
            "Lets any signed-in user submit feedback from their profile page. "
            "Turning this off only closes new submissions — feedback already "
            "on file stays visible to staff."
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
        return "Feedback settings"


class SeoSettings(models.Model):
    """Singleton row (always pk=1 — see `load()`) holding the one knob
    for whether this instance wants search engines to index it at all,
    adjustable from Profile → Administration → Site & SEO without a
    redeploy — same pattern as BackupSettings/FeedbackSettings above.

    Defaults to *disallowed*: this app runs entirely on the operator's
    own infrastructure and holds another person's private health data
    (CLAUDE.md's own "self-hosted, mobile-first fitness tracker") —
    the safe-by-default choice is a search engine never indexing it in
    the first place, not an operator having to remember to opt out
    the moment they stand up a fresh install. `apps.core.
    context_processors.seo` reads this into every template's context
    (the `<meta name="robots">` tag, `robots.txt`'s own body); nothing
    caches it, so a change here takes effect on the very next request.
    """

    search_engine_indexing_enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Lets search engines crawl and index this instance. Off by default — most "
            "installs are a private, self-hosted app for one household, not a public "
            "site anyone should be searching for."
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
        return "SEO settings"


class Feedback(TimeStampedModel):
    """A free-text note from a user about the application itself (a bug,
    a request, a "this is confusing" — not fitness data), submitted from
    Profile → Feedback and visible only to staff (Profile → Administration
    → Feedback, or /admin/) — never to other regular users, and not tied
    to any support-ticket workflow beyond that: this is a one-way inbox,
    not a two-way conversation thread."""

    class Category(models.TextChoices):
        WORKOUTS = "workouts", _("Workouts")
        PROGRAMS = "programs", _("Programs")
        PROGRESS = "progress", _("Progress")
        MEASUREMENTS = "measurements", _("Body measurements")
        ACTIVITIES = "activities", _("Activities")
        ACCOUNT = "account", _("Account & profile")
        OTHER = "other", _("Other")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feedback_submissions",
    )
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.OTHER
    )
    subject = models.CharField(max_length=200)
    message = models.TextField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_category_display()}: {self.subject}"


class PushSubscription(TimeStampedModel):
    """One browser/device's Web Push subscription (docs/SECURITY.md
    "Web Push notifications") — a user can hold several (one per
    device they've enabled notifications on). `endpoint`/`p256dh_key`/
    `auth_key` are exactly the three fields the browser's own
    `PushSubscription.toJSON()` returns; `apps.core.push.
    send_push_notification` reassembles them into the shape
    `pywebpush.webpush`'s own `subscription_info` argument expects.
    Never exported through `apps.accounts.services.export_account_data`
    beyond `endpoint`/`created_at` — `p256dh_key`/`auth_key` are
    credential-like (the push service, and only the push service,
    needs them to route/decrypt a payload), the same reasoning
    `apps.api.models.ApiKey.key_hash` is excluded there."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="push_subscriptions", on_delete=models.CASCADE
    )
    endpoint = models.URLField(unique=True, max_length=500)
    p256dh_key = models.CharField(max_length=255)
    auth_key = models.CharField(max_length=255)

    def __str__(self):
        return f"Push subscription: {self.user.username}"
