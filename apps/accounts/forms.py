from zoneinfo import available_timezones

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")


class ProfileForm(forms.ModelForm):
    """Display preferences only — unit_system/timezone drive unit
    conversion (apps.core.units, apps.measurements.units,
    apps.activities.units) and "today"/date-range boundaries
    (docs/ARCHITECTURE.md "Units and precision") everywhere else in the
    app, but until now had no UI to actually change them after signup."""

    timezone = forms.ChoiceField(choices=sorted((tz, tz) for tz in available_timezones()))

    class Meta:
        model = User
        fields = ["unit_system", "timezone"]
