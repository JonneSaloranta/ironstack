
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import BackupSettings

# Restyles Django's own admin (templates/admin/base_site.html,
# static/css/admin_theme.css) to match IronStack's branding/palette
# instead of building a parallel custom admin page — see
# docs/ARCHITECTURE.md "API layer" for the same "don't duplicate an
# abstraction Django already provides" reasoning applied here. Site-wide,
# so it lives in apps.core rather than any single feature app.
admin.site.site_header = "IronStack"
admin.site.site_title = "IronStack"
admin.site.index_title = _("Administration")


@admin.register(BackupSettings)
class BackupSettingsAdmin(admin.ModelAdmin):
    """Singleton, same pattern as apps.api.admin.ApiSettingsAdmin — the
    profile page's Backups settings card (apps.core.forms.
    BackupSettingsForm) is the normal way to change these; this exists
    so an admin can also do it from /admin/, and so it's discoverable
    there at all. No add/delete: BackupSettings.load() always works
    from exactly one row (pk=1)."""

    list_display = ["enabled", "hour", "retention_count"]

    def has_add_permission(self, request):
        return not BackupSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
