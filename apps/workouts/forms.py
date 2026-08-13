from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core import units as core_units
from apps.exercises.services import visible_to as exercises_visible_to

from .models import ExerciseSet, PerformedExercise


class ExerciseSetForm(forms.ModelForm):
    """`weight` is entered and displayed in the user's preferred unit
    (apps.core.units) — converted to/from canonical kg storage here, not
    left to the template or view, the same pattern as
    apps.measurements.forms.BodyMeasurementForm.

    Unlike that form, an unbound "new set" form's starting value doesn't
    come from `self.instance` (there isn't one yet) but from an explicit
    `initial=` dict the view builds (last-logged weight, or a smart
    suggestion) — the view is responsible for converting that to display
    units before constructing this form, same as it must supply `user=`.
    """

    weight = forms.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        model = ExerciseSet
        fields = [
            "weight",
            "reps",
            "target_reps",
            "rpe",
            "rir",
            "is_failure",
            "is_warmup",
            "notes",
        ]
        labels = {
            "reps": _("reps"),
            "target_reps": _("Target reps"),
            "rpe": _("RPE"),
            "rir": _("RIR"),
            "is_failure": _("Failed set"),
            "is_warmup": _("Warm-up"),
            "notes": _("notes"),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        unit_system = getattr(user, "unit_system", "metric")
        self.fields["weight"].label = _("Weight (%(unit)s)") % {
            "unit": core_units.weight_unit_label(unit_system)
        }
        if self.instance.pk:
            self.initial["weight"] = core_units.kg_to_display(self.instance.weight, unit_system)

    def save(self, commit=True):
        instance = super().save(commit=False)
        unit_system = getattr(self.user, "unit_system", "metric")
        instance.weight = core_units.display_to_kg(self.cleaned_data["weight"], unit_system)
        if commit:
            instance.save()
        return instance


class PerformedExerciseAddForm(forms.ModelForm):
    class Meta:
        model = PerformedExercise
        fields = ["exercise"]
        labels = {"exercise": _("exercise")}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["exercise"].queryset = exercises_visible_to(user)
