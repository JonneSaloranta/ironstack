from django import forms
from django.utils.translation import gettext_lazy as _

from .models import BackupSettings, Feedback, FeedbackSettings, SeoSettings


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


class BackupUploadForm(forms.Form):
    """Profile → Administration → Backups' "Upload backup" card — lets
    an admin restore from a .tar.gz they downloaded earlier (from this
    instance or another one) instead of only ever picking from what's
    already sitting in BACKUP_DIR. Only a basic filename-shape check
    here; the real validation (readable archive, all three expected
    members present) happens in apps.core.backups.save_uploaded_backup,
    called from the view once this form's own clean() passes — kept
    out of the form the same way every other real validation in this
    app lives in a service function, not a form/view."""

    archive = forms.FileField(
        label=_("Backup file"),
        help_text=_(
            "A .tar.gz backup archive previously downloaded from an IronStack instance."
        ),
    )

    def clean_archive(self):
        archive = self.cleaned_data["archive"]
        if not archive.name.endswith(".tar.gz"):
            raise forms.ValidationError(_("Must be a .tar.gz file."))
        return archive


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


class SeoSettingsForm(forms.ModelForm):
    """Profile → Administration → Site & SEO's own settings card — see
    apps.core.models.SeoSettings for what the toggle actually does
    once saved."""

    class Meta:
        model = SeoSettings
        fields = ["search_engine_indexing_enabled"]
        labels = {"search_engine_indexing_enabled": _("Allow search engines to index this site")}
