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
        "total volume) and \"Recently active\" list (last time this user "
        "started a workout — see apps.analytics.achievements) are both "
        "shared across every user on this instance, so this controls "
        "whether *this* user's own data is included in what everyone "
        "sees, not whether they personally see either widget at all.",
    )
    show_name_to_others = models.BooleanField(
        default=True,
        help_text="A second, more granular privacy setting than "
        "show_achievements: whether other users on this instance ever see "
        "this user's first name (see public_display_name()) — the "
        "username itself is always shown regardless, since it was already "
        "visible everywhere show_achievements applies before this field "
        "existed. Off falls back to the username alone.",
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

    # Two-factor authentication (apps.accounts.twofactor,
    # apps.accounts.views.TwoFactorSetupView/TwoFactorVerifyView) — a
    # single TOTP secret per user, no separate "device" model, since
    # nothing here needs more than one authenticator at a time. The
    # secret is stored as plain text, deliberately: the server has to
    # be able to read it back to compute the expected code on every
    # login (unlike a password, this can't be one-way hashed), and this
    # project has no existing field-level-encryption infrastructure to
    # build that on top of without adding real scope beyond what was
    # asked — see docs/SECURITY.md "Two-factor authentication" for this
    # trade-off spelled out plainly rather than silently assumed.
    # `totp_secret` is set as soon as setup starts (so the QR code
    # shown mid-setup and the code the user submits to confirm it are
    # generated from the same value); `totp_enabled` only flips to True
    # once that confirmation succeeds, so an abandoned, never-confirmed
    # setup attempt never blocks a future login.
    totp_secret = models.CharField(max_length=32, blank=True, default="")
    totp_enabled = models.BooleanField(default=False)

    # apps.accounts.context_processors.onboarding / views.OnboardingView /
    # templates/accounts/_onboarding_modal.html — a one-time, skippable
    # prompt shown on whatever page a user lands on right after their
    # first login, asking for name/email/starting weight/units and
    # explaining what each is used for. False is the right default for
    # every *newly created* account; the migration that added this field
    # backfills True onto every account that already existed at that
    # point, so onboarding never retroactively appears for someone who
    # was already using the app before this feature shipped.
    onboarding_completed = models.BooleanField(default=False)

    def __str__(self):
        return self.username

    def public_display_name(self):
        """What OTHER users see for this user — the achievements
        carousel and "Recently active" list (apps.analytics.achievements),
        currently the only places one user's identity is ever shown to
        another. Username plus first name if `show_name_to_others` is on
        and a first name is actually set; the bare username otherwise.
        Distinct from this user's own dashboard greeting
        (apps.core.greetings), which always uses their first name
        directly — that's this user looking at their own name, not
        something shown to anyone else, so show_name_to_others doesn't
        apply there."""
        if self.show_name_to_others and self.first_name:
            return f"{self.username} ({self.first_name})"
        return self.username


class TwoFactorBackupCode(models.Model):
    """One single-use recovery code for a user who's enabled 2FA
    (apps.accounts.twofactor.generate_backup_codes) — the standard
    fallback for "I lost my authenticator device" that doesn't require
    an admin to intervene. Hashed with Django's own password hasher
    (`code_hash`, via make_password/check_password) rather than a fast
    digest like apps.api.models.ApiKey.key_hash's SHA-256: a backup
    code is entered as rarely as a password and deserves the same
    timing-attack-resistant, deliberately-slow treatment, unlike an API
    key sent on every single request where a fast hash matters for
    server load. The plain code itself is only ever shown once, right
    after generation — never stored anywhere, never recoverable."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="backup_codes"
    )
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Backup code for {self.user} ({'used' if self.used_at else 'unused'})"


_DEFAULT_DISCLAIMER_TEXT = (
    "This is a self-hosted, independently operated instance. The person "
    "or organization running it is not responsible for any data loss. "
    "Back up your data regularly."
)


class SiteDisclaimer(models.Model):
    """Singleton row (always pk=1 — see `load()`), the same pattern as
    apps.core.models.BackupSettings/FeedbackSettings — a footer note
    shown on the login and signup pages (apps.accounts.views.
    RateLimitedLoginView/SignupView), editable from /admin/ without a
    redeploy. Plain text, not translated per-viewer the way UI chrome
    is: it's operator-authored content specific to *this* instance
    (who's running it, what they will or won't be liable for), so
    unlike this app's own strings there's nothing for gettext to
    translate — an operator who wants it in another language edits it
    directly. Blank hides it entirely, for an operator who'd rather
    not show one at all."""

    text = models.TextField(
        default=_DEFAULT_DISCLAIMER_TEXT,
        blank=True,
        help_text=_(
            "Shown as a footer note on the login and signup pages. "
            "Leave blank to hide it entirely."
        ),
    )

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — deleting it would just silently recreate the default on next load()

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Site disclaimer"
