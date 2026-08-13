from django.contrib import admin

from .models import PersonalRecord


@admin.register(PersonalRecord)
class PersonalRecordAdmin(admin.ModelAdmin):
    list_display = ["user", "exercise", "record_type", "rep_count", "value", "achieved_at"]
    list_filter = ["record_type"]
    search_fields = ["exercise__name", "user__username"]
