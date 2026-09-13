from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import Equipment, Exercise, ExerciseImage, ExerciseImageSettings, MuscleGroup


@admin.register(MuscleGroup)
class MuscleGroupAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    search_fields = ["name"]


class ExerciseImageInline(admin.TabularInline):
    """The way a system exercise's images are managed — a user-facing
    upload control only exists for a user's own custom exercise (see
    apps.exercises.views.exercise_image_create's own docstring); system
    exercises are admin-only, same as every other field on them.
    `ExerciseImage.clean()` (run via this inline formset's own
    `ModelForm._post_clean()`) enforces `ExerciseImageSettings.
    max_images_per_exercise` here exactly the same way it does for
    that user-facing form — one rule, enforced wherever an image gets
    saved."""

    model = ExerciseImage
    extra = 1
    fields = ["image", "preview", "caption", "attribution", "order"]
    readonly_fields = ["preview"]

    def preview(self, obj):
        if not obj.pk or not obj.image:
            return ""
        return format_html(
            '<img src="{}" style="max-height:80px;max-width:120px;object-fit:contain;">',
            obj.image.url,
        )

    preview.short_description = _("Preview")


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ["name", "movement_type", "equipment", "owner", "active"]
    list_filter = ["movement_type", "active", "equipment"]
    search_fields = ["name"]
    autocomplete_fields = ["equipment"]
    filter_horizontal = ["primary_muscle_groups", "secondary_muscle_groups"]
    inlines = [ExerciseImageInline]


@admin.register(ExerciseImageSettings)
class ExerciseImageSettingsAdmin(admin.ModelAdmin):
    """Singleton, same pattern as apps.core.admin.BackupSettingsAdmin —
    the one place to change how many images an exercise may hold at
    once (there's no user-facing settings card for this, unlike
    Backups/Feedback/SEO, since it isn't a per-installation preference
    so much as a fixed operator policy)."""

    list_display = ["max_images_per_exercise"]

    def has_add_permission(self, request):
        return not ExerciseImageSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
