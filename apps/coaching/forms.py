"""Form pieces shared by everything a personal trainer shares with
clients (apps.programs.forms.ProgramForm, apps.nutrition.forms.
DietPlanShareForm)."""

from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from apps.core.widgets import SearchablePickerWidget


def active_clients_of(coach):
    """The coach's *currently active* clients — the only people a plan
    can be shared with."""
    return get_user_model().objects.filter(
        coaches__coach=coach, coaches__ended_at__isnull=True
    ).order_by("username")


class ClientPickerField(forms.ModelMultipleChoiceField):
    """Pick clients by searching for them and adding them to a visible,
    removable list (SearchablePickerWidget). Shown by the name the client
    lets others see (User.public_display_name)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("queryset", get_user_model().objects.none())
        kwargs.setdefault("required", False)
        kwargs.setdefault(
            "widget",
            SearchablePickerWidget(
                placeholder=_("Search your clients…"),
                empty_text=_("Not shared with anyone — only you can see it."),
                no_match_text=_("No matching clients."),
            ),
        )
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        return obj.public_display_name()
