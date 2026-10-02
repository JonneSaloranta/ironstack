from django.contrib import admin

from .models import AssistantSettings, Conversation, UsageRecord


@admin.register(AssistantSettings)
class AssistantSettingsAdmin(admin.ModelAdmin):
    filter_horizontal = ["allowed_users"]

    def has_add_permission(self, request):
        return not AssistantSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = ["date", "user", "key_source", "requests", "input_tokens", "output_tokens"]
    list_filter = ["key_source", "date"]
    search_fields = ["user__username"]
    readonly_fields = [
        "user",
        "date",
        "key_source",
        "requests",
        "input_tokens",
        "output_tokens",
    ]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    """Metadata only — what people talk to the assistant about is their
    own business, the same as their messages to each other (apps.social
    registers no message admin either)."""

    list_display = ["user", "provider", "model", "key_source", "created_at", "updated_at"]
    list_filter = ["provider", "key_source"]
    search_fields = ["user__username"]
    fields = ["user", "provider", "model", "key_source", "created_at", "updated_at"]
    readonly_fields = fields

    def has_add_permission(self, request):
        return False
