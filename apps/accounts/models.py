import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class UnitSystem(models.TextChoices):
    METRIC = "metric", _("Metric (kg, km)")
    IMPERIAL = "imperial", _("Imperial (lb, mi)")


class EncryptedTextField(models.TextField):
    """Transparently encrypted at rest with settings.TOTP_ENCRYPTION_KEY
    (Fernet) once that's configured — see User.totp_secret's own
    comment for why this one field is the deliberate exception to
    "this app doesn't field-level-encrypt data" (docs/SECURITY.md
    "Two-factor authentication"). Every call site reads/writes
    `user.totp_secret` as a plain string exactly as before this field
    type existed (`pyotp.TOTP(user.totp_secret)`, `user.totp_secret =
    twofactor.generate_totp_secret()`, ...) — encryption/decryption
    happens here, once, rather than at every one of those.

    TextField, not CharField: a Fernet token is a fair bit longer than
    the ~32-character base32 secret it wraps, comfortably past
    CharField's max_length=32 the column used before this existed.

    Tolerates a value already in the column that *isn't* a Fernet
    token — a row written back before TOTP_ENCRYPTION_KEY was ever
    configured, or before this field type existed at all — by
    returning it unchanged rather than raising. That also means
    turning the key on doesn't need its own data migration: the next
    ordinary save() of a given row (2FA setup, or apps.accounts.
    management.commands.encrypt_existing_totp_secrets for every row at
    once) is what actually encrypts it, from then on.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value or not settings.TOTP_ENCRYPTION_KEY:
            return value
        return Fernet(settings.TOTP_ENCRYPTION_KEY).encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value or not settings.TOTP_ENCRYPTION_KEY:
            return value
        try:
            return Fernet(settings.TOTP_ENCRYPTION_KEY).decrypt(value.encode()).decode()
        except InvalidToken:
            # Not a Fernet token at all (a legacy plaintext secret —
            # see this class's own docstring), or the wrong key. Either
            # way, surfacing it as the raw stored value here rather
            # than raising means a wrong key fails obviously and
            # safely later (an unverifiable TOTP code at login), not
            # with an opaque exception on every single read of `user`.
            return value


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
    # nothing here needs more than one authenticator at a time.
    # Encrypted at rest with settings.TOTP_ENCRYPTION_KEY once that's
    # configured (EncryptedTextField above) — off by default, same as
    # BACKUP_ENCRYPTION_KEY, since the server still has to be able to
    # read the secret back to compute the expected code on every login
    # (unlike a password, this can't be one-way hashed) regardless of
    # whether a key is configured; see docs/SECURITY.md "Two-factor
    # authentication" for the honest limit of what this key protects
    # against (a database-only compromise, not one that also reaches
    # this instance's own .env). `totp_secret` is set as soon as setup
    # starts (so the QR code shown mid-setup and the code the user
    # submits to confirm it are generated from the same value);
    # `totp_enabled` only flips to True once that confirmation
    # succeeds, so an abandoned, never-confirmed setup attempt never
    # blocks a future login.
    totp_secret = EncryptedTextField(blank=True, default="")
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

    # apps.accounts.oidc.IronStackOIDCAuthenticationBackend — set True
    # the first time this account authenticates via Authentik, whether
    # that's the login that created it or a later one that matched it
    # to an existing local-password account by email (see that
    # backend's own docstring). Purely informational (a "linked to
    # Authentik" note on the profile page, docs/SECURITY.md "Single
    # sign-on (Authentik / OIDC)") — it never gates anything on its
    # own, since PASSWORD_LOGIN_ENABLED already controls whether local
    # password login works at all, independent of any one account's
    # history.
    is_sso_user = models.BooleanField(default=False)

    # apps.accounts.forms.ProfileForm / templates/accounts/profile.html —
    # off by default, unlike every other display/privacy toggle on this
    # model: turning it on doesn't just change what this instance itself
    # shows, it makes the *browser* fetch gravatar_url() directly from
    # gravatar.com on every profile page load, which is the only place
    # in this app that talks to a server outside the user's own
    # infrastructure (see docs/SECURITY.md "Gravatar profile picture").
    # That request hands gravatar.com this user's IP address and a
    # hash of their email, so it has to be something the user opts into
    # rather than something this app does for them automatically.
    show_gravatar = models.BooleanField(default=False)

    # apps.social — opt-*out* (both default True) rather than opt-in,
    # since neither one exposes anything by itself the way
    # show_gravatar does: it only gates whether *other* users on this
    # instance can start something with you (a friend request, a
    # direct group invite), not whether anything about you becomes
    # visible. Asked during onboarding (apps.accounts.forms.
    # OnboardingForm) the same way unit_system/timezone already are,
    # and editable afterward from Profile (ProfileForm).
    allow_friend_requests = models.BooleanField(
        default=True,
        help_text="Off stops other users on this instance from sending you "
        "a friend request at all. Doesn't affect friend requests you "
        "already have, or friendships you already made.",
    )
    allow_group_invites = models.BooleanField(
        default=True,
        help_text="Off stops a group member from inviting you to a group "
        "directly. You can still join any group yourself using its invite "
        "link, if you have one.",
    )

    def __str__(self):
        return self.username

    def gravatar_url(self, size=80):
        """The Gravatar (https://gravatar.com) picture for this
        account's email, if one exists — shown on the profile page only
        when `show_gravatar` is on (see that field's own comment for
        why it defaults off). `d=404` asks Gravatar to respond 404
        instead of falling back to a generated placeholder, so the
        <img> in the template can use `onerror` to just disappear for
        an email with no Gravatar account, rather than showing
        Gravatar's own default image for one that was never asked
        for."""
        email_hash = hashlib.sha256(self.email.strip().lower().encode("utf-8")).hexdigest()
        return f"https://www.gravatar.com/avatar/{email_hash}?s={size}&d=404"

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
