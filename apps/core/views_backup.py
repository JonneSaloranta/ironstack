"""Admin-only backup management, linked from the profile page — see
apps.core.backups for what actually happens and why restore here
carries real risk that scripts/restore.sh's version doesn't."""

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import TemplateView, View

from apps.core import backups as backup_services
from apps.core import version as version_services
from apps.core.forms import BackupSettingsForm, BackupUploadForm
from apps.core.mixins import StaffRequiredMixin
from apps.core.models import BackupSettings


class BackupListView(StaffRequiredMixin, TemplateView):
    template_name = "core/backup_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_backups = backup_services.list_backups()
        # Automatic (scheduler-made) backups get their own section,
        # separate from manual/uploaded ones — a daily schedule
        # produces a fundamentally different kind of list (many,
        # similar, low-effort) than a person deliberately clicking
        # "Create backup" or uploading a file (few, each intentional).
        # Only the single newest automatic one shows outright; the
        # rest sit behind a collapsed "N earlier automatic backups"
        # toggle the template renders (Alpine x-show, no server round-
        # trip) right below it.
        scheduled = [b for b in all_backups if b["source"] == "scheduled"]
        context["newest_scheduled"] = scheduled[0] if scheduled else None
        context["older_scheduled_backups"] = scheduled[1:]
        context["manual_backups"] = [b for b in all_backups if b["source"] != "scheduled"]
        context.setdefault(
            "settings_form", BackupSettingsForm(instance=BackupSettings.load())
        )
        context.setdefault("upload_form", BackupUploadForm())
        return context

    def post(self, request, *args, **kwargs):
        # Three distinct forms share this one page/URL — "Create
        # backup", "Upload backup", and the settings card below them —
        # told apart by which submit button was actually clicked
        # (name="action").
        action = request.POST.get("action")
        if action == "save_settings":
            return self._save_settings(request)
        if action == "upload_backup":
            return self._upload_backup(request)
        name = backup_services.create_backup()
        messages.success(request, _("Backup created: %(name)s") % {"name": name})
        return redirect("backup-list")

    def _save_settings(self, request):
        form = BackupSettingsForm(request.POST, instance=BackupSettings.load())
        if form.is_valid():
            form.save()
            messages.success(request, _("Backup settings saved."))
            return redirect("backup-list")
        # Re-render with the invalid form's own errors rather than
        # redirecting, the same as any other form on this app.
        context = self.get_context_data(settings_form=form)
        return self.render_to_response(context)

    def _upload_backup(self, request):
        form = BackupUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                name = backup_services.save_uploaded_backup(form.cleaned_data["archive"])
            except backup_services.InvalidBackupArchive:
                form.add_error(
                    "archive",
                    _(
                        "Not a valid IronStack backup archive — expected a .tar.gz "
                        "containing database.dump, media.tar, and manifest.json."
                    ),
                )
            else:
                messages.success(request, _("Backup uploaded: %(name)s") % {"name": name})
                # Straight into the same confirm-then-restore flow a
                # server-created backup uses — the whole point of
                # uploading one is almost always to restore it right
                # away.
                return redirect("backup-restore", name=name)
        context = self.get_context_data(upload_form=form)
        return self.render_to_response(context)


class BackupDownloadView(StaffRequiredMixin, View):
    def get(self, request, name):
        try:
            path = backup_services.safe_archive_path(name)
        except backup_services.InvalidBackupName:
            raise Http404 from None
        return FileResponse(open(path, "rb"), as_attachment=True, filename=name)


class BackupDeleteView(StaffRequiredMixin, View):
    """Only ever removes a copy sitting in storage, never anything
    actually running — unlike restore, that means no manifest-
    comparison confirm page of its own, just the same JS confirm() on
    the button every other delete in this app uses."""

    def post(self, request, name):
        try:
            backup_services.delete_backup(name)
        except backup_services.InvalidBackupName:
            raise Http404 from None
        messages.success(request, _("Backup deleted: %(name)s") % {"name": name})
        return redirect("backup-list")


class BackupRestoreView(StaffRequiredMixin, TemplateView):
    """GET shows the backup's own manifest next to the running
    instance's, plus the warning every destructive confirm page in
    this app uses (see templates/programs/program_confirm_delete.html)
    — a second, JS `confirm()` dialog on the submit button on top of
    that, given how much more severe this action is than an ordinary
    delete. POST actually performs the restore."""

    template_name = "core/backup_restore_confirm.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        name = kwargs["name"]
        try:
            context["backup_manifest"] = backup_services.read_manifest(name)
        except (backup_services.InvalidBackupName, FileNotFoundError, KeyError):
            raise Http404 from None
        context["name"] = name
        context["running_manifest"] = {
            "version": version_services.get_version(),
            "git_sha": version_services.get_git_sha(),
            "migrations": version_services.get_migration_state(),
        }
        return context

    def post(self, request, *args, **kwargs):
        name = kwargs["name"]
        backup_services.restore_backup(name)
        messages.success(request, _("Restored from backup: %(name)s") % {"name": name})
        return redirect(reverse("profile"))
