from django.contrib import admin

from .models import ExercisePrescription, Program, Workout


class ExercisePrescriptionInline(admin.TabularInline):
    model = ExercisePrescription
    extra = 0


class WorkoutInline(admin.TabularInline):
    model = Workout
    extra = 0
    show_change_link = True


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "is_template", "version"]
    list_filter = ["is_template"]
    search_fields = ["name"]
    inlines = [WorkoutInline]


@admin.register(Workout)
class WorkoutAdmin(admin.ModelAdmin):
    list_display = ["name", "program", "order", "scheduled_weekday"]
    list_filter = ["scheduled_weekday"]
    inlines = [ExercisePrescriptionInline]
