"""Forms for the nutrition onboarding wizard — see docs/NUTRITION.md
"Phased implementation plan" step 3. Each step is a small, focused
form (spec: "a step-by-step onboarding," not one giant form); the
wizard view (apps.nutrition.views) accumulates answers in the session
between steps and commits everything atomically on the last one.
"""

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core import units as core_units

from . import energy
from .models import ActivityJob, ActivityLevel, BiologicalSex, GoalType


class BodyStepForm(forms.Form):
    biological_sex = forms.ChoiceField(
        choices=BiologicalSex.choices,
        label=_("Biological sex"),
        help_text=_(
            "Used by the BMR formula (Mifflin-St Jeor) — a physiological input, not a "
            "gender-identity question."
        ),
    )
    birth_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}), label=_("Date of birth")
    )
    height = forms.DecimalField(max_digits=6, decimal_places=1, min_value=Decimal("50"))
    weight = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("20"))

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        is_metric = user.unit_system == "metric"
        self.fields["height"].label = _("Height (cm)") if is_metric else _("Height (in)")
        self.fields["weight"].label = _("Current weight (%(unit)s)") % {
            "unit": core_units.weight_unit_label(user.unit_system)
        }
        if user.height is not None:
            display = (
                core_units.meters_to_cm(user.height)
                if is_metric
                else core_units.meters_to_inches(user.height)
            )
            self.initial["height"] = display.quantize(Decimal("0.1"))

    def canonical_height_m(self):
        height = self.cleaned_data["height"]
        is_metric = self.user.unit_system == "metric"
        return core_units.cm_to_meters(height) if is_metric else core_units.inches_to_meters(height)

    def canonical_weight_kg(self):
        return core_units.display_to_kg(self.cleaned_data["weight"], self.user.unit_system)


class ActivityInputsForm(forms.Form):
    activity_job = forms.ChoiceField(choices=ActivityJob.choices, label=_("Your job"))
    daily_steps = forms.IntegerField(
        required=False, min_value=0, label=_("Average daily steps (if you know it)")
    )
    training_sessions_per_week = forms.IntegerField(
        required=False, min_value=0, max_value=14, label=_("Gym sessions per week")
    )
    training_session_minutes = forms.IntegerField(
        required=False, min_value=0, label=_("Typical session length (minutes)")
    )
    other_exercise_minutes_per_week = forms.IntegerField(
        required=False, min_value=0, label=_("Other exercise (minutes/week)")
    )
    self_reported_daily_calories = forms.IntegerField(
        required=False,
        min_value=0,
        label=_("Current daily calories, if you already track this"),
        help_text=_("Used only to compare against this app's own estimate, never as an input."),
    )


class ActivityLevelConfirmForm(forms.Form):
    activity_level = forms.ChoiceField(choices=ActivityLevel.choices, label=_("Activity level"))


class GoalStepForm(forms.Form):
    # x-model/@change: apps.nutrition.energy.DEFAULT_RATE_KG_PER_WEEK is
    # embedded as JSON in the template (see DEFAULT_RATES_JSON_SAFE
    # below) so picking a goal instantly fills in that goal's default
    # rate client-side — no round-trip needed just to see a sensible
    # starting number, matching the rest of this app's Alpine.js usage
    # for small, purely-presentational state.
    goal_type = forms.ChoiceField(
        choices=GoalType.choices,
        label=_("Goal"),
        widget=forms.Select(attrs={"x-model": "goalType", "@change": "rate = rates[goalType]"}),
    )
    target_weight = forms.DecimalField(
        max_digits=8, decimal_places=2, required=False, min_value=Decimal("20")
    )
    target_rate = forms.DecimalField(
        max_digits=5, decimal_places=3, widget=forms.NumberInput(attrs={"x-model": "rate"})
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        unit_label = core_units.weight_unit_label(user.unit_system)
        self.fields["target_weight"].label = _("Target weight (%(unit)s)") % {"unit": unit_label}
        self.fields["target_rate"].label = _("Target rate (kg/week)")
        self.fields["target_rate"].help_text = _(
            "Negative to lose weight, positive to gain. Pre-filled from your goal below — "
            "capped at a safe rate for your bodyweight."
        )

    def canonical_target_weight_kg(self):
        value = self.cleaned_data.get("target_weight")
        if value is None:
            return None
        return core_units.display_to_kg(value, self.user.unit_system)


DEFAULT_RATES_JSON_SAFE = {
    goal_type.value: str(rate) for goal_type, rate in energy.DEFAULT_RATE_KG_PER_WEEK.items()
}
