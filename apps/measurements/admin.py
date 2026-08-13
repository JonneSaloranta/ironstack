from django.contrib import admin

from .models import BodyMeasurement, MeasurementType


@admin.register(MeasurementType)
class MeasurementTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "unit_kind", "owner", "active"]
    list_filter = ["unit_kind", "active"]
    search_fields = ["name"]


@admin.register(BodyMeasurement)
class BodyMeasurementAdmin(admin.ModelAdmin):
    list_display = ["user", "measurement_type", "value", "recorded_at"]
    list_filter = ["measurement_type"]
    search_fields = ["user__username"]
