from django import forms

from apps.exercises.services import visible_to as exercises_visible_to

from .models import ExercisePrescription, Program, Workout


class ProgramForm(forms.ModelForm):
    class Meta:
        model = Program
        fields = ["name", "description", "is_template"]
        labels = {
            "is_template": "Save as a personal template",
        }
        help_texts = {
            "is_template": (
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
            "target_weight": "Target weight (kg)",
            "target_rpe": "Target RPE",
            "target_rir": "Target RIR",
            "weight_increment": "Weight increment (kg)",
            "percentage_target": "Percentage target (% 1RM)",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Only exercises this user can actually see (system + own custom)
        # may be prescribed — mirrors apps.exercises visibility rules.
        self.fields["exercise"].queryset = exercises_visible_to(user)

    def clean(self):
        cleaned_data = super().clean()
        min_reps = cleaned_data.get("min_reps")
        max_reps = cleaned_data.get("max_reps")
        if min_reps and max_reps and min_reps > max_reps:
            self.add_error("min_reps", "Minimum reps cannot exceed maximum reps.")
        return cleaned_data
