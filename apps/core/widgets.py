"""Shared form widgets."""

from django import forms


class SearchablePickerWidget(forms.CheckboxSelectMultiple):
    """A multiple choice as "search, then add to a visible list": typing
    in a search box suggests matching choices, picking one adds it to a
    list where each entry has its own remove button
    (templates/core/widgets/searchable_picker.html,
    static/js/searchable-picker.js).

    It is still a CheckboxSelectMultiple underneath: the checkboxes are
    rendered as the no-JavaScript fallback and the script only checks and
    unchecks them, so the submitted data — and every view and form using
    it — is exactly what a plain checkbox list would send.

    `placeholder`, `empty_text` (nothing picked yet) and `no_match_text`
    are the widget's own wording, since what's being picked varies."""

    template_name = "core/widgets/searchable_picker.html"

    def __init__(self, *args, placeholder="", empty_text="", no_match_text="", **kwargs):
        super().__init__(*args, **kwargs)
        self.placeholder = placeholder
        self.empty_text = empty_text
        self.no_match_text = no_match_text

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"].update(
            placeholder=self.placeholder,
            empty_text=self.empty_text,
            no_match_text=self.no_match_text,
        )
        return context

    def id_for_label(self, id_, index=None):
        # The field's <label> names the search box, not the first checkbox.
        if index is None:
            return id_
        return super().id_for_label(id_, index)
