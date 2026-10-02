from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from apps.core.widgets import SearchablePickerWidget

from .models import AssistantSettings
from .services import MAX_MESSAGE_LENGTH


class MessageForm(forms.Form):
    text = forms.CharField(
        label=_("Message"),
        max_length=MAX_MESSAGE_LENGTH,
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": _("Ask about your training or nutrition…")}
        ),
    )


class ApiKeyForm(forms.Form):
    api_key = forms.CharField(
        label=_("Anthropic API key"),
        max_length=300,
        strip=True,
        # render_value=False: a saved key is never sent back to the browser.
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text=_(
            "Create one at console.anthropic.com. It's stored encrypted and used only for "
            "your own conversations; usage is billed to your Anthropic account."
        ),
    )

    def clean_api_key(self):
        key = self.cleaned_data["api_key"]
        if not key.startswith("sk-ant-") or len(key) < 20 or any(c.isspace() for c in key):
            raise forms.ValidationError(_("That doesn't look like an Anthropic API key."))
        return key


class AssistantSettingsForm(forms.ModelForm):
    allowed_users = forms.ModelMultipleChoiceField(
        label=_("Selected users"),
        queryset=get_user_model().objects.filter(is_active=True).order_by("username"),
        required=False,
        widget=SearchablePickerWidget(
            placeholder=_("Search users…"),
            empty_text=_("Nobody selected."),
            no_match_text=_("No matching users."),
        ),
        help_text=_("Used when access is set to “Selected users”."),
    )

    class Meta:
        model = AssistantSettings
        fields = ["enabled", "shared_key_access", "allowed_users", "daily_token_limit"]
        labels = {
            "enabled": _("Assistant enabled"),
            "shared_key_access": _("Who may use this instance's AI model"),
            "daily_token_limit": _("Daily token limit per user"),
        }
