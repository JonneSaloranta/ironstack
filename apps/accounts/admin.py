from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import SiteDisclaimer, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Preferences", {"fields": ("unit_system", "timezone")}),
        # totp_secret is deliberately never in a fieldset here — an
        # admin needing to see it to help a user would defeat the
        # entire point of it being a secret; disabling it (below) is
        # the correct recovery path for someone locked out with no
        # working authenticator and no backup codes, not exposing it.
        (_("Two-factor authentication"), {"fields": ("totp_enabled",)}),
    )
    list_display = DjangoUserAdmin.list_display + ("totp_enabled",)
    actions = [*DjangoUserAdmin.actions, "disable_two_factor"]

    @admin.action(description=_("Disable two-factor authentication for selected users"))
    def disable_two_factor(self, request, queryset):
        # Support/recovery path for "I lost my authenticator device
        # and my backup codes" — the one situation the self-service
        # flows (apps.accounts.views.TwoFactorDisableView/
        # TwoFactorVerifyView) can't handle on their own, since both
        # require either the password *and* a working second factor,
        # or the password alone but never bypass the second factor
        # entirely. Clears backup codes too, the same as the self-
        # service disable does — they're meaningless without
        # totp_enabled anyway.
        for user in queryset.filter(totp_enabled=True):
            user.totp_enabled = False
            user.totp_secret = ""
            user.save(update_fields=["totp_enabled", "totp_secret"])
            user.backup_codes.all().delete()


@admin.register(SiteDisclaimer)
class SiteDisclaimerAdmin(admin.ModelAdmin):
    """Singleton, same pattern as apps.core.admin's BackupSettingsAdmin/
    FeedbackSettingsAdmin — this is the only way to edit the login/
    signup pages' footer disclaimer, there's no in-app settings card
    for it (it's operator-facing content, not a per-user preference)."""

    list_display = ["__str__"]

    def has_add_permission(self, request):
        return not SiteDisclaimer.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
