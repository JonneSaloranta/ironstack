from datetime import timedelta

from django import forms

from . import units
from .models import Activity, ActivityType


class ActivityTypeForm(forms.ModelForm):
    class Meta:
        model = ActivityType
        fields = ["name"]


class ActivityForm(forms.ModelForm):
    """`duration_minutes` and `distance` aren't logged in the units the
    model stores them in — a plain minutes count is far faster to enter
    on mobile than a HH:MM:SS field (docs/UI.md "quick entry ... with as
    few taps as possible"), and distance is entered/shown in the user's
    preferred unit, not canonical meters. Both are converted here, same
    pattern as apps.measurements.forms.BodyMeasurementForm.
    """

    duration_minutes = forms.IntegerField(min_value=1, label="Duration (minutes)")
    distance = forms.DecimalField(max_digits=8, decimal_places=2, required=False)

    class Meta:
        model = Activity
        fields = [
            "date",
            "start_time",
            "duration_minutes",
            "distance",
            "calories",
            "notes",
        ]

    def __init__(self, *args, user=None, activity_type=None, **kwargs):
        self.user = user
        self.activity_type = activity_type
        super().__init__(*args, **kwargs)
        if user is not None:
            label = units.distance_unit_label(user.unit_system)
            self.fields["distance"].label = f"Distance ({label})"
        if self.instance.pk:
            self.initial["duration_minutes"] = int(self.instance.duration.total_seconds() // 60)
            if self.instance.distance is not None and user is not None:
                self.initial["distance"] = units.distance_to_display(
                    self.instance.distance, user.unit_system
                )

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.user = self.user
        instance.activity_type = self.activity_type
        instance.duration = timedelta(minutes=self.cleaned_data["duration_minutes"])
        distance_value = self.cleaned_data.get("distance")
        instance.distance = (
            units.distance_to_canonical(distance_value, self.user.unit_system)
            if distance_value is not None
            else None
        )
        if commit:
            instance.save()
        return instance
