from django import forms
from django.utils.translation import gettext as _gettext
from django.utils.translation import gettext_lazy as _

from apps.core import units as core_units
from apps.core.formatting import (
    ONE_RM_FULL,
    RIR_FULL,
    RPE_FULL,
    abbr_label,
    lazy_format_html,
)
from apps.exercises.services import visible_to as exercises_visible_to

from .models import ExercisePrescription, Program, Workout


class ProgramForm(forms.ModelForm):
    class Meta:
        model = Program
        fields = ["name", "description", "is_template"]
        labels = {
            "is_template": _("Save as a personal template"),
        }
        help_texts = {
            "is_template": _(
                "Templates aren't meant to be run directly — copy them into a "
                "new program (from the program page) whenever you start a new cycle, "
                "keeping the original untouched."
            ),
        }


class WorkoutForm(forms.ModelForm):
    class Meta:
        model = Workout
        fields = ["name", "order", "scheduled_weekday", "notes"]


class ExercisePrescriptionForm(forms.ModelForm):
    """`target_weight` and `weight_increment` are entered and displayed in
    the user's preferred unit (apps.core.units) — converted to/from
    canonical kg storage here, not left to the template or view, the same
    pattern as apps.measurements.forms.BodyMeasurementForm. The model
    field itself stays "canonical kg" (see its help_text) regardless.
    """

    target_weight = forms.DecimalField(max_digits=6, decimal_places=2, required=False)
    weight_increment = forms.DecimalField(max_digits=6, decimal_places=2, required=False)

    class Meta:
        model = ExercisePrescription
        fields = [
            "exercise",
            "order",
            "set_count",
            "min_reps",
            "max_reps",
            "target_weight",
            "target_rpe",
            "target_rir",
            "progression_method",
            "weight_increment",
            "percentage_target",
            "notes",
        ]
        labels = {
            "exercise": _("exercise"),
            "target_rpe": lazy_format_html(
                "{} {}", _("Target"), abbr_label(_("RPE"), RPE_FULL)
            ),
            "target_rir": lazy_format_html(
                "{} {}", _("Target"), abbr_label(_("RIR"), RIR_FULL)
            ),
            "percentage_target": lazy_format_html(
                "{} (% {})", _("Percentage target"), abbr_label(_("1RM"), ONE_RM_FULL)
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        # Only exercises this user can actually see (system + own custom)
        # may be prescribed — mirrors apps.exercises visibility rules.
        self.fields["exercise"].queryset = exercises_visible_to(user)
        # ModelChoiceField renders each <option> via str(exercise) by
        # default (Exercise.__str__ returns self.name) — that bypasses
        # the template layer entirely, so {% trans %} there can't reach
        # it. label_from_instance is the documented hook for controlling
        # that per-option text; gettext() (not the module's lazy `_`)
        # since this runs per-request, with translation already active,
        # not at class-definition time. A no-op for a user's own custom
        # exercise (never in the seed catalog — see
        # apps.exercises.i18n_content), same as everywhere else this
        # pattern is used.
        self.fields["exercise"].label_from_instance = lambda obj: _gettext(obj.name)
        unit_system = getattr(user, "unit_system", "metric")
        unit_label = core_units.weight_unit_label(unit_system)
        self.fields["target_weight"].label = _("Target weight (%(unit)s)") % {"unit": unit_label}
        self.fields["weight_increment"].label = _("Weight increment (%(unit)s)") % {
            "unit": unit_label
        }
        if self.instance.pk:
            if self.instance.target_weight is not None:
                self.initial["target_weight"] = core_units.kg_to_display(
                    self.instance.target_weight, unit_system
                )
            if self.instance.weight_increment is not None:
                self.initial["weight_increment"] = core_units.kg_to_display(
                    self.instance.weight_increment, unit_system
                )

    def save(self, commit=True):
        instance = super().save(commit=False)
        unit_system = getattr(self.user, "unit_system", "metric")
        target_weight = self.cleaned_data.get("target_weight")
        instance.target_weight = (
            core_units.display_to_kg(target_weight, unit_system)
            if target_weight is not None
            else None
        )
        weight_increment = self.cleaned_data.get("weight_increment")
        instance.weight_increment = (
            core_units.display_to_kg(weight_increment, unit_system)
            if weight_increment is not None
            else None
        )
        if commit:
            instance.save()
        return instance

    def clean(self):
        cleaned_data = super().clean()
        min_reps = cleaned_data.get("min_reps")
        max_reps = cleaned_data.get("max_reps")
        if min_reps and max_reps and min_reps > max_reps:
            self.add_error("min_reps", _("Minimum reps cannot exceed maximum reps."))
        return cleaned_data
