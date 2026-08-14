from django.contrib import admin

from .models import ApiKey, ApiKeyPermission, ApiSettings, RateLimitTier


@admin.register(RateLimitTier)
class RateLimitTierAdmin(admin.ModelAdmin):
    """The admin-adjustable rate-limit knobs the original request asked
    for — editing requests_per_minute/requests_per_day here takes effect
    immediately (apps.api.throttling reads them fresh every request, no
    caching, no redeploy needed)."""

    list_display = ["name", "requests_per_minute", "requests_per_day", "is_default"]

    def save_model(self, request, obj, form, change):
        # Exactly one tier should ever be the default (apps.api.services
        # .default_tier() assumes this) — enforced here rather than a DB
        # constraint, since it's a rule about the *set* of rows, not any
        # single row's own validity.
        if obj.is_default:
            RateLimitTier.objects.exclude(pk=obj.pk).update(is_default=False)
        super().save_model(request, obj, form, change)


@admin.register(ApiSettings)
class ApiSettingsAdmin(admin.ModelAdmin):
    """Singleton — the "max 10 (säädettävä) api avainta" cap, and
    anything else instance-wide that later needs to be admin-tunable
    without a redeploy. No add/delete: apps.api.models.ApiSettings.load()
    always works from exactly one row (pk=1)."""

    list_display = ["max_api_keys_per_user"]

    def has_add_permission(self, request):
        return not ApiSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class ApiKeyPermissionInline(admin.TabularInline):
    model = ApiKeyPermission
    extra = 0
    can_delete = False
    fields = ["context", "can_create", "can_read", "can_update", "can_delete"]


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    """key_hash is deliberately never shown or editable here — same
    reasoning as a password hash: nothing legitimate is ever done with
    it except equality-checked lookups in apps.api.auth, and displaying
    it would just be one more place a leaked screenshot/access log entry
    could matter."""

    list_display = ["name", "user", "prefix", "tier", "is_active", "last_used_at", "created_at"]
    list_filter = ["is_active", "tier"]
    search_fields = ["name", "prefix", "user__username"]
    autocomplete_fields = ["user"]
    readonly_fields = ["key_hash", "prefix", "created_at", "last_used_at"]
    inlines = [ApiKeyPermissionInline]
