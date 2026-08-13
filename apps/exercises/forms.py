from django import forms

from .models import Exercise


class ExerciseForm(forms.ModelForm):
    class Meta:
        model = Exercise
        fields = [
            "name",
            "description",
            "primary_muscle_groups",
            "secondary_muscle_groups",
            "equipment",
            "movement_type",
            "weight_input_mode",
        ]
        widgets = {
            "primary_muscle_groups": forms.CheckboxSelectMultiple,
            "secondary_muscle_groups": forms.CheckboxSelectMultiple,
        }
