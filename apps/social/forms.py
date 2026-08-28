from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Group

MESSAGE_MAX_LENGTH = 4000


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name", "description"]
        labels = {"name": _("Name"), "description": _("Description")}
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class MessageForm(forms.Form):
    """Shared by both the direct-message and group-message threads —
    the two underlying models (DirectMessage/GroupMessage) differ, but
    the one field a user actually fills in doesn't, so one plain Form
    (not a ModelForm tied to either model) avoids maintaining two
    near-identical copies of it."""

    body = forms.CharField(
        label=_("Message"),
        max_length=MESSAGE_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 2, "autofocus": True}),
    )


class GroupInviteForm(forms.Form):
    """A group member inviting one of their own friends who isn't
    already in the group — the queryset is built per-request in the
    view (apps.social.views_groups), not here, since it depends on
    both the acting user's friend list and this specific group's
    current membership."""

    friend = forms.ModelChoiceField(queryset=None, label=_("Friend"))

    def __init__(self, *args, choices_queryset, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["friend"].queryset = choices_queryset
