from decimal import Decimal
from zoneinfo import available_timezones

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UserCreationForm
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from apps.core import units as core_units
from apps.core.formatting import BMI_FULL, abbr_label, lazy_format_html

from . import twofactor
from .models import UnitSystem, User

# apps.accounts.views.RateLimitedLoginView's brute-force protection —
# no relation to apps.api's rate limiting, which is a completely
# separate, API-key-only mechanism with its own admin-configurable
# tiers. This one is fixed and simple on purpose: it exists to blunt
# automated password guessing, not to be a tunable product feature.
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60

# apps.accounts.views.RateLimitedPasswordResetView's own protection —
# without it, PasswordResetView (django.contrib.auth) is wide open to
# being used as a free tool to spam an arbitrary email address with
# reset links over and over, using this instance's own SMTP relay to
# do it. Separate counter/window from the login limiter above: a
# password-reset request and a failed login are different actions with
# different legitimate retry rates.
PASSWORD_RESET_ATTEMPT_LIMIT = 5
PASSWORD_RESET_ATTEMPT_WINDOW_SECONDS = 15 * 60

# apps.accounts.views.TwoFactorVerifyView's own protection — a 6-digit
# TOTP code only has a million possible values, so this login step
# needs its own brute-force limit just as much as the password one
# does. Keyed by *user*, not client IP (see TwoFactorVerifyForm.
# clean_code below): by this stage the attacker already has a correct
# password and a specific account in mind, so limiting by IP alone
# would let them route around it trivially, and there's no legitimate-
# user harm in tying the limit to the one account actually being
# verified.
TWOFACTOR_ATTEMPT_LIMIT = 5
TWOFACTOR_ATTEMPT_WINDOW_SECONDS = 5 * 60

# `zoneinfo.available_timezones()` includes a handful of non-geographic
# aliases alongside real IANA "Area/Location" zones. Both are
# specifically misleading rather than just unfamiliar, so they're
# dropped from the picker instead of merely being oddly-named options:
# "localtime" sounds like it means "detect and use my device's own
# timezone" but is actually a *fixed* server-side alias (whatever
# /etc/localtime resolves to on the machine running this container,
# typically UTC in a minimal Docker image) — nothing about it is
# dynamic, and a server-rendered app has no way to know a visiting
# device's timezone without separate client-side detection this app
# doesn't do (see apps.accounts.middleware.UserTimezoneMiddleware for
# why picking a real zone, e.g. "Europe/Helsinki", is what actually
# makes the "Timezone" setting work). "Factory" is tzdata's own
# placeholder for "no real zone configured" — never a meaningful choice
# for a user to make.
_MISLEADING_TIMEZONE_ALIASES = {"localtime", "Factory"}


class SignupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")


def _client_ip(request):
    """The `X-Real-IP` header `compose/nginx/nginx.conf` sets to
    `$remote_addr` — nginx overwrites this unconditionally rather than
    forwarding whatever a client sent, so it can't be spoofed by a
    request that goes through that proxy. `REMOTE_ADDR` on its own
    would be nginx's *own* container IP for every proxied request
    (docker-compose.yml never publishes a port for `web` directly —
    only `nginx` is reachable from outside), which would make every
    visitor share one counter. Falls back to REMOTE_ADDR for direct,
    no-proxy access (e.g. `runserver` in dev)."""
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "unknown")


class _RateLimitedLoginMixin:
    """Shared by RateLimitedAuthenticationForm and
    RateLimitedAdminAuthenticationForm below — blocks further login
    attempts from the same client for `window_seconds` after `limit`
    failed attempts within that window. Keyed by client IP, not the
    submitted username: keying by username alone would let an attacker
    cycle through guessed usernames from one source freely, and would
    let an attacker lock a *real* user out on purpose by deliberately
    failing their login from elsewhere — a denial-of-service against
    that one account. IP-based keying costs the attacker actual
    infrastructure to route around instead. Uses the same shared
    DatabaseCache apps.api's throttling does, for the same reason:
    gunicorn runs multiple worker processes with no shared memory, so
    an in-process counter would let each worker serve its own
    independent allowance.

    `cache_key_prefix` is distinct per subclass deliberately — a
    regular login attempt and an admin login attempt from the same IP
    (plausible on a shared/office network) don't count against each
    other's allowance, since they're different endpoints with a
    different risk/legitimate-retry profile.
    """

    cache_key_prefix = "login-attempts"
    limit = LOGIN_ATTEMPT_LIMIT
    window_seconds = LOGIN_ATTEMPT_WINDOW_SECONDS

    def clean(self):
        cache_key = f"{self.cache_key_prefix}:{_client_ip(self.request)}"
        attempts = cache.get(cache_key, 0)
        if attempts >= self.limit:
            raise forms.ValidationError(
                _("Too many failed login attempts. Try again in a few minutes."),
                code="rate_limited",
            )
        try:
            cleaned_data = super().clean()
        except forms.ValidationError:
            cache.set(cache_key, attempts + 1, self.window_seconds)
            raise
        cache.delete(cache_key)
        return cleaned_data


class RateLimitedAuthenticationForm(_RateLimitedLoginMixin, AuthenticationForm):
    pass


class RateLimitedAdminAuthenticationForm(_RateLimitedLoginMixin, AdminAuthenticationForm):
    """Django's own /admin/ login (AdminSite.login()) has its own,
    completely separate login view/form from the one
    RateLimitedAuthenticationForm above protects — it's never routed
    through django.contrib.auth.urls or apps.accounts.views.
    RateLimitedLoginView at all, so without this, brute-forcing
    /admin/login/ directly was wide open even after the regular login
    got rate-limited. Wired in via apps.core.admin's
    `admin.site.login_form = RateLimitedAdminAuthenticationForm`.
    AdminSite.login() internally delegates to django.contrib.auth.
    views.LoginView (passing this as `authentication_form`), which is
    what actually supplies the `request` this form's clean() needs —
    no extra view-level wiring required the way the non-admin login
    needed RateLimitedLoginView.get_form_kwargs (LoginView already
    does that itself)."""

    cache_key_prefix = "admin-login-attempts"


class RateLimitedPasswordResetForm(PasswordResetForm):
    """Blocks further password-reset requests from the same client
    after PASSWORD_RESET_ATTEMPT_LIMIT within
    PASSWORD_RESET_ATTEMPT_WINDOW_SECONDS. Keyed by client IP for the
    same reason RateLimitedAuthenticationForm above is: keying by the
    submitted email would let an attacker lock a real user's own
    ability to request a reset by repeatedly submitting *their* address
    from elsewhere. Every submission counts here, not just failed
    ones — unlike a login attempt, there's no such thing as a "failed"
    password-reset request to only count selectively (the form is
    deliberately valid, and sends no email, for an address that
    doesn't exist — see its own save(), unchanged here — so counting
    only "failures" would count nothing at all).

    django.contrib.auth.forms.PasswordResetForm doesn't accept a
    `request` kwarg the way AuthenticationForm does, so
    apps.accounts.views.RateLimitedPasswordResetView.get_form_kwargs
    injects one explicitly for this subclass to use.
    """

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request

    def clean(self):
        cache_key = f"password-reset-attempts:{_client_ip(self.request)}"
        attempts = cache.get(cache_key, 0)
        if attempts >= PASSWORD_RESET_ATTEMPT_LIMIT:
            raise forms.ValidationError(
                _("Too many password reset requests. Try again in a few minutes."),
                code="rate_limited",
            )
        cache.set(cache_key, attempts + 1, PASSWORD_RESET_ATTEMPT_WINDOW_SECONDS)
        return super().clean()


class AccountDetailsForm(forms.ModelForm):
    """Editable account identity — username, name, email — kept
    deliberately separate from ProfileForm's display preferences
    (unit_system/timezone/height/etc.) and from the password itself
    (django.contrib.auth's own PasswordChangeForm/view already handles
    that, with its own re-authentication requirement that a plain
    ModelForm save shouldn't bypass for these fields)."""

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]


class ProfileForm(forms.ModelForm):
    """Display preferences — unit_system/timezone drive unit conversion
    (apps.core.units, apps.measurements.units, apps.activities.units) and
    "today"/date-range boundaries (docs/ARCHITECTURE.md "Units and
    precision") everywhere else in the app — plus `height`, entered and
    displayed in the user's preferred unit (cm/inches) the same way
    apps.measurements.forms.BodyMeasurementForm handles a length reading,
    converted to/from canonical meters here. `height` is optional and
    exists solely to compute BMI (apps.core.bmi) alongside a logged body
    weight — nothing else in the app reads it. `show_bmi` is a separate
    on/off switch for the BMI card itself, shown on the "Body weight"
    measurement history page (apps.measurements), not here: some people
    would simply rather not see the number at all, regardless of
    whether height/weight exist to compute it. `show_achievements` is
    a privacy setting, not a
    display one: the dashboard's achievements carousel and "Recently
    active" list (apps.analytics.achievements) are both shared across
    every user on this instance, so this controls whether *this* user's
    own data (longest streak/workout count/PRs/total weight lifted, and
    when they last started a workout) is included in what everyone
    sees — off keeps their own activity private while they still see
    everyone else's. `show_name_to_others` is a second, more granular
    privacy setting: whether this user's first name is ever shown
    alongside their username in that same carousel/list
    (`User.public_display_name()`) — the username itself is always
    shown regardless, unaffected by this toggle.
    """

    timezone = forms.ChoiceField(
        choices=sorted(
            (tz, tz)
            for tz in available_timezones()
            if tz not in _MISLEADING_TIMEZONE_ALIASES
        ),
        label=_("Timezone"),
    )
    height = forms.DecimalField(max_digits=6, decimal_places=1, required=False)

    class Meta:
        model = User
        fields = [
            "unit_system",
            "timezone",
            "height",
            "show_bmi",
            "show_achievements",
            "show_name_to_others",
            "language",
        ]
        labels = {
            "unit_system": _("Units"),
            "show_bmi": lazy_format_html(
                "{} {} {}",
                _("Show"),
                abbr_label(_("BMI"), BMI_FULL),
                _("on the body weight page"),
            ),
            "show_achievements": _("Share my activity"),
            "show_name_to_others": _("Show my name to others"),
            "language": _("Language"),
        }
        help_texts = {
            "show_bmi": _("Turns off the BMI card and its category ranges entirely."),
            "show_achievements": _(
                "Lets everyone using this instance see your longest streak, "
                "workout count, PRs, total weight lifted, and when you last "
                "trained, in the dashboard's achievements carousel and "
                "\"Recently active\" list. Turn off to keep your own "
                "activity private — you'll still see everyone else's."
            ),
            "show_name_to_others": _(
                "Shows your first name next to your username wherever "
                "others can see it (the achievements carousel and "
                "\"Recently active\" list). Turn off to show just your "
                "username there, like before this setting existed."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Captured before any submitted data can change unit_system on
        # the instance, so a height typed under "cm" is always converted
        # as cm even if the same submission also switches the unit
        # system — matches what the user actually saw on the label.
        self._unit_system = self.instance.unit_system
        is_metric = self._unit_system == UnitSystem.METRIC
        self.fields["height"].label = (
            _("Height (cm)") if is_metric else _("Height (in)")
        )
        if self.instance.height is not None:
            # Canonical storage keeps 0.1mm precision (see the model
            # field's help_text) — quantized down here to what's
            # actually worth displaying, same as
            # apps.measurements.units.to_display does for a length
            # reading.
            display = (
                core_units.meters_to_cm(self.instance.height)
                if is_metric
                else core_units.meters_to_inches(self.instance.height)
            )
            self.initial["height"] = display.quantize(Decimal("0.1"))

    def save(self, commit=True):
        instance = super().save(commit=False)
        height = self.cleaned_data.get("height")
        if height is not None:
            instance.height = (
                core_units.cm_to_meters(height)
                if self._unit_system == UnitSystem.METRIC
                else core_units.inches_to_meters(height)
            )
        else:
            instance.height = None
        if commit:
            instance.save()
        return instance


class TwoFactorSetupConfirmForm(forms.Form):
    """Profile → Two-factor authentication → setup's own confirm step
    — proves the user actually configured their authenticator app
    correctly (scanned the right QR code, device clock close enough to
    the server's) before `totp_enabled` ever flips to True. No rate
    limiting here unlike TwoFactorVerifyForm below: this only ever runs
    against an already-authenticated user's own just-generated secret
    (shown to them moments earlier), not as a login gate an outside
    attacker could brute-force."""

    code = forms.CharField(label=_("Verification code"), max_length=6)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_code(self):

        code = self.cleaned_data["code"].strip()
        if not twofactor.verify_totp_code(self.user.totp_secret, code):
            raise forms.ValidationError(
                _("Incorrect code — check your authenticator app and try again."),
                code="invalid",
            )
        return code


class TwoFactorVerifyForm(forms.Form):
    """The login flow's second step (apps.accounts.views.
    TwoFactorVerifyView) — one field takes either a 6-digit TOTP code
    or a backup code (apps.accounts.twofactor.verify_and_consume_backup_code),
    told apart by shape: a plain 6-digit value tries TOTP first, since
    that's what a normal, working login uses every time; anything else
    (or a 6-digit value that doesn't verify — someone who mistyped a
    backup code that happened to be all digits, however unlikely) falls
    back to a backup-code lookup."""

    code = forms.CharField(label=_("Verification code"))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_code(self):

        cache_key = f"2fa-attempts:{self.user.pk}"
        attempts = cache.get(cache_key, 0)
        if attempts >= TWOFACTOR_ATTEMPT_LIMIT:
            raise forms.ValidationError(
                _("Too many incorrect codes. Try again in a few minutes."),
                code="rate_limited",
            )

        code = self.cleaned_data["code"].strip()
        verified = False
        if code.isdigit() and len(code) == 6:
            verified = twofactor.verify_totp_code(self.user.totp_secret, code)
        if not verified:
            verified = twofactor.verify_and_consume_backup_code(self.user, code)

        if not verified:
            cache.set(cache_key, attempts + 1, TWOFACTOR_ATTEMPT_WINDOW_SECONDS)
            raise forms.ValidationError(_("Incorrect code."), code="invalid")
        cache.delete(cache_key)
        return code


class TwoFactorDisableForm(forms.Form):
    """Profile → Two-factor authentication → "Disable" — requires the
    account's own password, not just a JS confirm() like most other
    destructive actions in this app: unlike deleting a workout or a
    backup, turning 2FA off is a real security-relevant change that a
    hijacked-but-not-fully-compromised session (e.g. someone briefly at
    an unlocked, logged-in device) shouldn't be able to do with a
    single tap."""

    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError(_("Incorrect password."), code="invalid")
        return password


class OnboardingForm(forms.Form):
    """apps.accounts.views.OnboardingView / templates/accounts/
    _onboarding_modal.html — the one-time, entirely optional prompt
    shown on whatever page a user lands on right after their first
    login. Deliberately a plain Form, not a ModelForm: it writes to
    both `User` (name/email/units/height) and, for weight,
    apps.measurements.models.BodyMeasurement — the same "Body weight"
    system measurement type apps.measurements' own logging form uses,
    so a weight entered here shows up on the Body weight history page
    exactly like any other logged reading, not a separate, special
    "onboarding weight" field on User itself. `height` stays a plain
    User field (same as ProfileForm's own), since it's one-off context
    for BMI rather than a reading logged repeatedly over time the way
    weight is. Every field is optional (required=False) — the whole
    point of a skippable prompt is that leaving any single field blank
    is a completely normal outcome, not a validation error."""

    first_name = forms.CharField(
        max_length=150,
        required=False,
        label=_("First name"),
        help_text=_(
            "Used to personalize your dashboard greeting, and next to your "
            "username wherever your activity is shown to others (if you "
            "allow that in your profile)."
        ),
    )
    email = forms.EmailField(
        required=False,
        label=_("Email"),
        help_text=_(
            "Used only for password-reset emails if you ever get locked "
            "out. Never shown to other users."
        ),
    )
    weight = forms.DecimalField(
        max_digits=8,
        decimal_places=2,
        required=False,
        min_value=0,
        help_text=_(
            "Logged as your first body-weight entry, so your progress "
            "charts have a starting point. Leave blank and log it anytime "
            "from the Body weight page instead."
        ),
    )
    # Same field/conversion shape as ProfileForm's own `height` — stored
    # on User (canonical meters), not a BodyMeasurement, since it's
    # optional context for BMI (apps.core.bmi) rather than a repeated
    # reading over time the way weight is.
    height = forms.DecimalField(
        max_digits=6,
        decimal_places=1,
        required=False,
        min_value=0,
        help_text=_(
            "Used together with a logged body weight to show your BMI. "
            "Leave blank to skip this — you can add it anytime from your "
            "profile."
        ),
    )
    unit_system = forms.ChoiceField(
        choices=UnitSystem.choices,
        label=_("Units"),
        help_text=_(
            "Sets whether weights and distances are shown in kg/km or "
            "lb/mi throughout the app. Change this anytime from your "
            "profile."
        ),
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["first_name"].initial = user.first_name
        self.fields["email"].initial = user.email
        self.fields["unit_system"].initial = user.unit_system
        unit_label = core_units.weight_unit_label(user.unit_system)
        self.fields["weight"].label = (
            _("Current weight (%(unit)s)") % {"unit": unit_label}
            if unit_label
            else _("Current weight")
        )
        self.fields["height"].label = (
            _("Height (cm)") if user.unit_system == UnitSystem.METRIC else _("Height (in)")
        )

    def save(self):
        # Local imports: apps.measurements depends on apps.accounts
        # (apps.measurements.units imports apps.accounts.models.
        # UnitSystem), so importing it at module level here would risk
        # a circular import the moment anything in apps.measurements
        # ever needed apps.accounts.forms — nothing does today, but
        # keeping the dependency one-directional at module-load time
        # costs nothing and avoids relying on import order.
        from apps.measurements import units as measurement_units
        from apps.measurements.models import BodyMeasurement, MeasurementType

        user = self.user
        user.first_name = self.cleaned_data["first_name"]
        user.email = self.cleaned_data["email"]
        user.unit_system = self.cleaned_data["unit_system"]

        height = self.cleaned_data.get("height")
        if height is not None:
            user.height = (
                core_units.cm_to_meters(height)
                if user.unit_system == UnitSystem.METRIC
                else core_units.inches_to_meters(height)
            )

        user.onboarding_completed = True
        user.save(
            update_fields=[
                "first_name",
                "email",
                "unit_system",
                "height",
                "onboarding_completed",
            ]
        )

        weight = self.cleaned_data.get("weight")
        if weight:
            measurement_type = MeasurementType.objects.filter(
                name="Body weight", owner=None
            ).first()
            # Absent only if an operator deleted the system-seeded type
            # entirely (apps.measurements lets a user deactivate but
            # never delete it) — skip silently rather than error out of
            # an otherwise-successful save over one optional field.
            if measurement_type is not None:
                BodyMeasurement.objects.create(
                    user=user,
                    measurement_type=measurement_type,
                    value=measurement_units.to_canonical(
                        weight, measurement_type.unit_kind, user.unit_system
                    ),
                )
