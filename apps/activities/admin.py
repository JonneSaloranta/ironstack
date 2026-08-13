from django.contrib import admin

from .models import Activity, ActivityType


@admin.register(ActivityType)
class ActivityTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "active"]
    list_filter = ["active"]
    search_fields = ["name"]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ["user", "activity_type", "date", "duration", "distance"]
    list_filter = ["activity_type"]
    search_fields = ["user__username"]
