from django.contrib import admin

from .models import Equipment, Exercise, MuscleGroup


@admin.register(MuscleGroup)
class MuscleGroupAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ["name", "movement_type", "equipment", "owner", "active"]
    list_filter = ["movement_type", "active", "equipment"]
    search_fields = ["name"]
    autocomplete_fields = ["equipment"]
    filter_horizontal = ["primary_muscle_groups", "secondary_muscle_groups"]
