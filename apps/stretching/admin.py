from django.contrib import admin

from .models import PerformedStretch, RoutineItem, Stretch, StretchRoutine, StretchSession


@admin.register(Stretch)
class StretchAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "per_side", "default_hold_seconds", "owner", "active"]
    list_filter = ["kind", "active"]
    search_fields = ["name"]
    filter_horizontal = ["muscle_groups"]


class RoutineItemInline(admin.TabularInline):
    model = RoutineItem
    extra = 0


@admin.register(StretchRoutine)
class StretchRoutineAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "active"]
    list_filter = ["active"]
    search_fields = ["name"]
    inlines = [RoutineItemInline]


class PerformedStretchInline(admin.TabularInline):
    model = PerformedStretch
    extra = 0


@admin.register(StretchSession)
class StretchSessionAdmin(admin.ModelAdmin):
    list_display = ["user", "name", "date", "status", "duration"]
    list_filter = ["status"]
    search_fields = ["user__username"]
    inlines = [PerformedStretchInline]
