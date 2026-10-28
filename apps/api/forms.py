from django import forms
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from .models import ApiContext

# (ApiKeyPermission field name, column label) - the order here is the
# column order in the create-key permission grid (apps.api.views_web,
# templates/api/key_form.html).
CRUD_VERBS = [
    ("can_create", _("Create")),
    ("can_read", _("Read")),
    ("can_update", _("Update")),
    ("can_delete", _("Delete")),
]


class ApiKeyCreateForm(forms.Form):
    """`name` plus one checkbox per (context, CRUD verb) - one row per
    `ApiContext` member × 4 verbs, added dynamically in `__init__`
    rather than hand-written since the context list is a single
    source of truth (apps.api.models.ApiContext) - adding a new
    context (e.g. `nutrition`) needs no change here at all.
    `permissions()` reshapes the flat checkbox fields back into the
    `{context: {can_create: bool, ...}}` dict
    apps.api.services.create_api_key/set_permissions expect.
    """

    name = forms.CharField(
        max_length=100, label=_("Name"), help_text=_("A label to tell your keys apart.")
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for context_value, context_label in ApiContext.choices:
            for verb_field, verb_label in CRUD_VERBS:
                # The grid shows the verb as a column header and the
                # context as a row header, so the checkbox has no
                # <label> of its own: name it for screen readers.
                self.fields[f"{context_value}__{verb_field}"] = forms.BooleanField(
                    required=False,
                    label=verb_label,
                    widget=forms.CheckboxInput(
                        attrs={"aria-label": format_lazy("{} - {}", verb_label, context_label)}
                    ),
                )

    def permissions(self):
        return {
            context_value: {
                verb_field: self.cleaned_data.get(f"{context_value}__{verb_field}", False)
                for verb_field, _verb_label in CRUD_VERBS
            }
            for context_value, _context_label in ApiContext.choices
        }
