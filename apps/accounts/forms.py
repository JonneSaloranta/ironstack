from decimal import Decimal
from zoneinfo import available_timezones

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils.translation import gettext_lazy as _

from apps.core import units as core_units
from apps.core.formatting import BMI_FULL, abbr_label, lazy_format_html

from .models import UnitSystem, User

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
    on/off switch for the BMI card itself: some people would simply
    rather not see the number at all, regardless of whether height/weight
    exist to compute it. `show_achievements` is a privacy setting, not a
    display one: the dashboard's achievements carousel and "Recently
    active" list (apps.analytics.achievements) are both shared across
    every user on this instance, so this controls whether *this* user's
    own data (longest streak/workout count/PRs/total weight lifted, and
    when they last started a workout) is included in what everyone
    sees — off keeps their own activity private while they still see
    everyone else's.
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
            "language",
        ]
        labels = {
            "unit_system": _("Units"),
            "show_bmi": lazy_format_html(
                "{} {} {}", _("Show"), abbr_label(_("BMI"), BMI_FULL), _("on the dashboard")
            ),
            "show_achievements": _("Share my activity"),
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
