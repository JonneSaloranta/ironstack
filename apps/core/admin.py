
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import BackupSettings, Feedback, FeedbackSettings

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


@admin.register(FeedbackSettings)
class FeedbackSettingsAdmin(admin.ModelAdmin):
    """Singleton, same pattern as BackupSettingsAdmin above — Profile →
    Administration → Feedback's own settings card is the normal way to
    change this; this exists so an admin can also do it from /admin/."""

    list_display = ["enabled"]

    def has_add_permission(self, request):
        return not FeedbackSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    """Read-mostly inbox — Profile → Administration → Feedback
    (apps.core.views_feedback.FeedbackListView) is the normal way staff
    browse this; this exists so it's also reachable from /admin/,
    filterable/searchable there in ways the plain list page doesn't
    offer."""

    list_display = ["created_at", "user", "category", "subject"]
    list_filter = ["category", "created_at"]
    search_fields = ["subject", "message", "user__username"]
    readonly_fields = ["user", "category", "subject", "message", "created_at", "updated_at"]

    def has_add_permission(self, request):
        return False
