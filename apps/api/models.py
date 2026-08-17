from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class ApiContext(models.TextChoices):
    """Every resource area an API key's permissions can be scoped to —
    the "profiili, ohjelmat, liikkeet jne" grouping, one flag per
    context rather than per individual model/endpoint, since that's the
    granularity a user actually thinks in when deciding what a key
    should be allowed to touch. Maps 1:1 onto apps.api.views' viewsets
    (each declares `api_context = ApiContext.X`) and this app's key
    creation form (one CRUD row per context — see apps.api.forms).

    A couple of contexts have no create/update/delete route at all
    (profile is a singleton, records are derived/immutable — see
    docs/API.md "Contexts"), so their can_create/can_update/can_delete
    flags are accepted but simply have nothing to authorize; left in
    for a uniform permission model rather than special-cased away.
    """

    PROFILE = "profile", _("Profile")
    EXERCISES = "exercises", _("Exercises")
    PROGRAMS = "programs", _("Programs")
    WORKOUTS = "workouts", _("Workouts")
    MEASUREMENTS = "measurements", _("Measurements")
    ACTIVITIES = "activities", _("Activities")
    RECORDS = "records", _("Records")
    ANALYTICS = "analytics", _("Analytics")
    NUTRITION = "nutrition", _("Nutrition")


class RateLimitTier(models.Model):
    """An admin-defined, admin-editable rate-limit profile — the
    "ratelimit tier" the request asked for. Editing a row's numbers here
    takes effect immediately for every key on that tier, no redeploy —
    see apps.api.throttling, which reads these two fields fresh on every
    request rather than baking a rate into settings.py.
    """

    name = models.CharField(max_length=50, unique=True)
    requests_per_minute = models.PositiveIntegerField(default=60)
    requests_per_day = models.PositiveIntegerField(default=5000)
    # Assigned to every newly created key unless an admin picks a
    # different tier for it afterward (apps.api.services.create_api_key)
    # — exactly one tier should carry this at a time, enforced in
    # ApiKeyAdmin.save_model rather than a DB constraint, since "exactly
    # one" is a business rule about the *set* of rows, not a single
    # row's own validity.
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["requests_per_minute"]

    def __str__(self):
        return f"{self.name} ({self.requests_per_minute}/min, {self.requests_per_day}/day)"


class ApiSettings(models.Model):
    """Singleton row (always pk=1 — see `load()`) holding instance-wide
    API knobs an admin can tune from the Django admin without a
    redeploy, the same "adjustable without touching code" requirement
    RateLimitTier serves for rate limits.
    """

    max_api_keys_per_user = models.PositiveIntegerField(
        default=10,
        help_text="How many API keys a single user may have active at once.",
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
        return "API settings"


class ApiKey(TimeStampedModel):
    """A user-issued credential for apps.api — see apps.api.crypto for
    how the secret itself is generated/hashed. `key_hash` is what
    authentication actually looks up against; the raw secret is never
    stored anywhere and is shown to its owner exactly once, at creation
    (apps.api.views_web.ApiKeyCreateView) — a leaked database dump alone
    can't be used to authenticate, the same reasoning as a password hash.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="api_keys", on_delete=models.CASCADE
    )
    name = models.CharField(
        max_length=100, help_text="A label to tell your keys apart — not shown to anyone else."
    )
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    # First few characters of the raw secret, kept in the clear so the
    # key list can show *something* identifying without ever storing or
    # re-displaying the full secret — the same convention GitHub/Stripe
    # etc. use for their own API key management UIs.
    prefix = models.CharField(max_length=12, db_index=True)
    tier = models.ForeignKey(
        RateLimitTier, on_delete=models.PROTECT, related_name="api_keys"
    )
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"


class ApiKeyPermission(models.Model):
    """One row per (key, context): which CRUD verbs that key may use
    against that context — see apps.api.permissions.HasContextPermission,
    which is the only thing that ever reads these.
    """

    api_key = models.ForeignKey(
        ApiKey, related_name="permissions", on_delete=models.CASCADE
    )
    context = models.CharField(max_length=20, choices=ApiContext.choices)
    can_create = models.BooleanField(default=False)
    can_read = models.BooleanField(default=False)
    can_update = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)

    class Meta:
        ordering = ["api_key_id", "context"]
        constraints = [
            models.UniqueConstraint(
                fields=["api_key", "context"], name="unique_api_key_context"
            )
        ]

    def __str__(self):
        return f"{self.api_key.name} — {self.get_context_display()}"
