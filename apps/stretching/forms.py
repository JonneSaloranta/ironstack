from datetime import timedelta

from django import forms
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.exercises.models import MuscleGroup

from . import services
from .models import MAX_HOLD_SECONDS, RoutineItem, Stretch, StretchRoutine, StretchSession

NUMERIC = {"inputmode": "numeric", "autocomplete": "off"}


class _OwnedNameMixin:
    """Per-owner name uniqueness, checked up front — the model's
    UniqueConstraint covers `owner`, which isn't a form field, so
    ModelForm's own constraint validation skips it and a duplicate would
    otherwise surface as an IntegrityError."""

    def __init__(self, *args, owner=None, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        duplicate = (
            self._meta.model.objects.filter(owner=self.owner, name__iexact=name)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if duplicate:
            raise forms.ValidationError(gettext("You already have one with this name."))
        return name

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.owner = self.owner
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class StretchRoutineForm(_OwnedNameMixin, forms.ModelForm):
    class Meta:
        model = StretchRoutine
        fields = ["name", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class StretchForm(_OwnedNameMixin, forms.ModelForm):
    class Meta:
        model = Stretch
        fields = [
            "name",
            "kind",
            "per_side",
            "default_hold_seconds",
            "muscle_groups",
            "instructions",
        ]
        widgets = {
            "default_hold_seconds": forms.NumberInput(attrs=NUMERIC),
            "muscle_groups": forms.CheckboxSelectMultiple,
            "instructions": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["muscle_groups"].queryset = MuscleGroup.objects.order_by("name")
        # Rendered through {% trans %} like everywhere else muscle
        # group names appear (they're seeded canonical English).
        self.fields["muscle_groups"].label_from_instance = lambda group: gettext(group.name)


class RoutineItemForm(forms.ModelForm):
    """Adding or editing one stretch in a routine. `hold_seconds` may be
    left blank when adding — the stretch's own default is used."""

    hold_seconds = forms.IntegerField(
        min_value=5,
        max_value=MAX_HOLD_SECONDS,
        required=False,
        label=_("Hold (seconds)"),
        widget=forms.NumberInput(attrs=NUMERIC),
    )

    class Meta:
        model = RoutineItem
        fields = ["stretch", "hold_seconds", "sets", "rest_seconds"]
        widgets = {
            "sets": forms.NumberInput(attrs=NUMERIC),
            "rest_seconds": forms.NumberInput(attrs=NUMERIC),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        stretch_field = self.fields["stretch"]
        stretch_field.queryset = services.visible_stretches(user)
        stretch_field.label_from_instance = lambda stretch: (
            gettext(stretch.name) if not stretch.is_custom else stretch.name
        )
        if self.instance.pk:
            # A routine item keeps pointing at its stretch even after
            # that stretch is retired; editing the other fields mustn't
            # suddenly fail validation because of that.
            stretch_field.disabled = True
            stretch_field.queryset = Stretch.objects.filter(pk=self.instance.stretch_id)

    def clean_hold_seconds(self):
        hold = self.cleaned_data.get("hold_seconds")
        if hold is None and self.instance.pk:
            raise forms.ValidationError(gettext("This field is required."))
        return hold


class QuickLogForm(forms.ModelForm):
    """Logging a session after the fact — just a duration, optionally
    tied to one of the user's visible routines."""

    duration_minutes = forms.IntegerField(
        min_value=1,
        max_value=600,
        label=_("Duration (minutes)"),
        widget=forms.NumberInput(attrs=NUMERIC),
    )

    class Meta:
        model = StretchSession
        fields = ["date", "routine", "duration_minutes", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"routine": _("Routine")}

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["routine"].required = False
        self.fields["routine"].queryset = services.visible_routines(user)
        self.fields["routine"].label_from_instance = lambda routine: (
            gettext(routine.name) if not routine.is_custom else routine.name
        )
        if self.instance.pk:
            if self.instance.duration is not None:
                self.initial["duration_minutes"] = max(
                    1, round(self.instance.duration.total_seconds() / 60)
                )
            # A finished session's routine is part of its history;
            # only the date, duration and notes stay editable.
            del self.fields["routine"]
        else:
            self.initial.setdefault("date", timezone.localdate())

    def save(self, commit=True):
        duration = timedelta(minutes=self.cleaned_data["duration_minutes"])
        if self.instance.pk:
            session = super().save(commit=False)
            session.duration = duration
            if commit:
                session.save()
            return session
        return services.quick_log(
            self.user,
            date=self.cleaned_data["date"],
            duration=duration,
            routine=self.cleaned_data.get("routine"),
            notes=self.cleaned_data.get("notes", ""),
        )
