from django.contrib import admin

from .models import ExerciseSet, PerformedExercise, WorkoutSession


class ExerciseSetInline(admin.TabularInline):
    model = ExerciseSet
    extra = 0


class PerformedExerciseInline(admin.TabularInline):
    model = PerformedExercise
    extra = 0
    show_change_link = True


@admin.register(WorkoutSession)
class WorkoutSessionAdmin(admin.ModelAdmin):
    list_display = ["user", "workout", "status", "started_at", "ended_at"]
    list_filter = ["status"]
    inlines = [PerformedExerciseInline]


@admin.register(PerformedExercise)
class PerformedExerciseAdmin(admin.ModelAdmin):
    list_display = ["exercise", "session", "order"]
    inlines = [ExerciseSetInline]
