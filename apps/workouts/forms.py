from django import forms

from apps.exercises.services import visible_to as exercises_visible_to

from .models import ExerciseSet, PerformedExercise


class ExerciseSetForm(forms.ModelForm):
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
            "target_reps": "Target reps",
            "rpe": "RPE",
            "rir": "RIR",
            "is_failure": "Failed set",
            "is_warmup": "Warm-up",
        }


class PerformedExerciseAddForm(forms.ModelForm):
    class Meta:
        model = PerformedExercise
        fields = ["exercise"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["exercise"].queryset = exercises_visible_to(user)
