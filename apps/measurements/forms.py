from django import forms
from django.utils.translation import gettext_lazy as _

from . import units
from .models import BodyMeasurement, MeasurementType


class MeasurementTypeForm(forms.ModelForm):
    class Meta:
        model = MeasurementType
        fields = ["name", "unit_kind"]


class BodyMeasurementForm(forms.ModelForm):
    """`value` is entered and displayed in the user's preferred unit
    (apps.measurements.units) — converted to/from canonical storage here,
    not left to the template or view.

    `recorded_at` uses a native datetime-local picker rather than plain
    text. That needs both a widget `format=` (for correctly pre-filling
    an existing value — Django's locale-dependent default doesn't match
    what the input expects) *and* explicit `input_formats` on the field
    itself: Django's default DATETIME_INPUT_FORMATS all use a space
    between date and time (e.g. "2026-01-15 08:30"), never datetime-local's
    "T" separator ("2026-01-15T08:30"), so a submitted value would fail
    to parse without this — the same pitfall this app's activity date/
    time fields hit, just for a combined field instead of two separate
    ones.
    """

    value = forms.DecimalField(max_digits=8, decimal_places=2)
    recorded_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        # Accept the picker's "T"-separated value and the plain
        # space-separated one Django defaults to elsewhere (e.g. a
        # script or API caller posting directly) — render always uses
        # the "T" format above regardless of which was submitted.
        input_formats=[
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ],
    )

    class Meta:
        model = BodyMeasurement
        fields = ["value", "recorded_at", "notes"]

    def __init__(self, *args, user=None, measurement_type=None, **kwargs):
        self.user = user
        self.measurement_type = measurement_type
        super().__init__(*args, **kwargs)
        if measurement_type is not None and user is not None:
            label = units.display_unit_label(measurement_type.unit_kind, user.unit_system)
            self.fields["value"].label = (
                _("Value (%(unit)s)") % {"unit": label} if label else _("Value")
            )
            if self.instance.pk:
                self.initial["value"] = units.to_display(
                    self.instance.value, measurement_type.unit_kind, user.unit_system
                )

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.user = self.user
        instance.measurement_type = self.measurement_type
        instance.value = units.to_canonical(
            self.cleaned_data["value"], self.measurement_type.unit_kind, self.user.unit_system
        )
        if commit:
            instance.save()
        return instance
