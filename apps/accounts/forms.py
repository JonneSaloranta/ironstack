from decimal import Decimal
from zoneinfo import available_timezones

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils.translation import gettext_lazy as _

from apps.core import units as core_units

from .models import UnitSystem, User


class SignupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")


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
    exist to compute it.
    """

    timezone = forms.ChoiceField(
        choices=sorted((tz, tz) for tz in available_timezones()), label=_("Timezone")
    )
    height = forms.DecimalField(max_digits=6, decimal_places=1, required=False)

    class Meta:
        model = User
        fields = ["unit_system", "timezone", "height", "show_bmi", "language"]
        labels = {
            "unit_system": _("Units"),
            "show_bmi": _("Show BMI on the dashboard"),
            "language": _("Language"),
        }
        help_texts = {
            "show_bmi": _("Turns off the BMI card and its category ranges entirely.")
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
