from django import forms

from . import units
from .models import BodyMeasurement, MeasurementType


class MeasurementTypeForm(forms.ModelForm):
    class Meta:
        model = MeasurementType
        fields = ["name", "unit_kind"]


class BodyMeasurementForm(forms.ModelForm):
    """`value` is entered and displayed in the user's preferred unit
    (apps.measurements.units) — converted to/from canonical storage here,
    not left to the template or view."""

    value = forms.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        model = BodyMeasurement
        fields = ["value", "recorded_at", "notes"]

    def __init__(self, *args, user=None, measurement_type=None, **kwargs):
        self.user = user
        self.measurement_type = measurement_type
        super().__init__(*args, **kwargs)
        if measurement_type is not None and user is not None:
            label = units.display_unit_label(measurement_type.unit_kind, user.unit_system)
            self.fields["value"].label = f"Value ({label})" if label else "Value"
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
