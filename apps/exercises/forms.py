from django import forms
from django.utils.translation import gettext

from .models import Exercise, ExerciseImage


class ExerciseForm(forms.ModelForm):
    class Meta:
        model = Exercise
        fields = [
            "name",
            "description",
            "instructions",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # MuscleGroup/Equipment names are seeded content translated via
        # apps.exercises.i18n_content (see docs/ARCHITECTURE.md
        # "Internationalization") — but a ModelChoiceField/
        # ModelMultipleChoiceField renders each option's text via
        # str(obj) internally, bypassing the template layer {% trans %}
        # relies on entirely. label_from_instance is the documented
        # hook for that, same pattern as apps.programs.forms.
        # ExercisePrescriptionForm/apps.workouts.forms.
        # PerformedExerciseAddForm use for their own exercise picker.
        # gettext (not the module-level lazy `_`) since this runs
        # per-request, with translation already active — a no-op for a
        # user's own custom exercise's checkboxes, there's nothing to
        # translate here that isn't seeded content anyway.
        for field_name in ("primary_muscle_groups", "secondary_muscle_groups", "equipment"):
            self.fields[field_name].label_from_instance = lambda obj: gettext(obj.name)


class ExerciseImageForm(forms.ModelForm):
    """Upload form for a custom exercise's own image gallery (apps.
    exercises.views.exercise_image_create) — `exercise` isn't a form
    field, the view sets it from the URL before validating, the same
    way `ExerciseCreateView.form_valid` sets `owner` rather than
    exposing it as something the submitter picks. Uploading past
    `ExerciseImageSettings.max_images_per_exercise` surfaces as a
    plain form error via `ExerciseImage.clean()` (Django's `ModelForm.
    _post_clean()` calls `instance.full_clean()` automatically), not a
    separate check duplicated here.
    """

    class Meta:
        model = ExerciseImage
        fields = ["image", "caption"]

    def clean_image(self):
        from . import services

        image = self.cleaned_data["image"]
        if image is None or not hasattr(image, "content_type"):
            return image  # unchanged (no new upload)
        if image.size > services.MAX_IMAGE_UPLOAD_BYTES:
            raise forms.ValidationError(
                gettext("That image is too large — the limit is %(megabytes)s MB.")
                % {"megabytes": services.MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)}
            )
        prepared, (self.instance.width, self.instance.height) = (
            services.prepare_uploaded_image(image, with_size=True)
        )
        return prepared
