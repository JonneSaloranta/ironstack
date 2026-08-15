from django import forms
from django.utils.translation import gettext_lazy as _

from .models import BackupSettings, Feedback, FeedbackSettings


class BackupSettingsForm(forms.ModelForm):
    """Profile → Administration → Backups' own settings card — see
    apps.core.models.BackupSettings for what each field actually does
    once saved. The 0-23 range on `hour` is already enforced by the
    model field's own MaxValueValidator (PositiveSmallIntegerField
    rules out negative values on its own), so the ModelForm inherits
    that validation for free — no need to repeat it here."""

    class Meta:
        model = BackupSettings
        fields = ["enabled", "hour", "retention_count"]
        labels = {
            "enabled": _("Automatic daily backups"),
            "hour": _("Backup hour (UTC)"),
            "retention_count": _("Keep the most recent"),
        }


class FeedbackForm(forms.ModelForm):
    """Profile → Feedback — the form any signed-in user fills in
    themselves; `user` is set from the request in the view, not exposed
    here as a field."""

    class Meta:
        model = Feedback
        fields = ["category", "subject", "message"]
        labels = {
            "category": _("Category"),
            "subject": _("Subject"),
            "message": _("Message"),
        }


class FeedbackSettingsForm(forms.ModelForm):
    """Profile → Administration → Feedback's own settings card — see
    apps.core.models.FeedbackSettings for what the toggle actually
    does once saved."""

    class Meta:
        model = FeedbackSettings
        fields = ["enabled"]
        labels = {"enabled": _("Accept new feedback")}
