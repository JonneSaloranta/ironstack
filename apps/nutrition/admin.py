from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from . import services
from .models import (
    DiaryEntry,
    DietPlan,
    DietPlanItem,
    DietPlanMeal,
    Food,
    MealSlot,
    NutritionGoal,
    NutritionProfile,
    NutritionTarget,
    OpenFoodFactsSettings,
    Recipe,
    RecipeIngredient,
)


@admin.register(NutritionProfile)
class NutritionProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "biological_sex", "activity_level"]
    search_fields = ["user__username"]


@admin.register(NutritionGoal)
class NutritionGoalAdmin(admin.ModelAdmin):
    list_display = ["user", "goal_type", "target_rate_kg_per_week", "started_at", "ended_at"]
    list_filter = ["goal_type"]
    search_fields = ["user__username"]


@admin.register(NutritionTarget)
class NutritionTargetAdmin(admin.ModelAdmin):
    list_display = ["user", "daily_calories", "source", "started_at", "ended_at"]
    list_filter = ["source"]
    search_fields = ["user__username"]


@admin.register(MealSlot)
class MealSlotAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "order", "active"]
    list_filter = ["active"]
    search_fields = ["name"]


@admin.register(Food)
class FoodAdmin(admin.ModelAdmin):
    """Two admin-only bulk actions, both asked for directly:

    `merge_selected_foods` — deleting a duplicate outright cascades
    away every DiaryEntry/RecipeIngredient/DietPlanItem that ever
    referenced it, which is exactly the kind of silent history loss
    CLAUDE.md warns against — re-points every reference from the
    duplicates onto one kept row instead, see
    apps.nutrition.services.merge_foods's own docstring. Django's
    default "Delete selected foods" action is left as-is (still
    reachable for a genuinely bogus row nothing references) — its own
    built-in confirmation page already lists every object that would
    cascade-delete before an admin confirms, real standard protection
    this feature doesn't need to duplicate.

    `refresh_selected_from_off` — force-refreshes selected foods from
    OpenFoodFacts immediately rather than waiting for the normal
    14-day staleness gate to trigger on next use. Still an explicit,
    admin-chosen selection, not the unconditional bulk re-sync this
    integration was deliberately scoped away from in the first place
    (docs/NUTRITION.md "OpenFoodFacts integration")."""

    list_display = [
        "name", "brand", "owner", "calories", "nutri_score", "nova_group", "off_id", "active",
    ]
    list_filter = ["active", "nutri_score", "nova_group"]
    search_fields = ["name", "brand", "off_id"]
    actions = ["merge_selected_foods", "refresh_selected_from_off"]

    @admin.action(description=_("Refresh selected foods from OpenFoodFacts"))
    def refresh_selected_from_off(self, request, queryset):
        # force=True bypasses the normal 14-day staleness gate
        # (docs/NUTRITION.md "OpenFoodFacts integration") — an
        # explicit admin request for these specific rows right now,
        # not the unconditional bulk re-sync this integration was
        # deliberately scoped away from in the first place (a
        # selection the admin made themselves, not "every OFF food
        # in the database on a timer").
        off_foods = list(queryset.exclude(off_id__isnull=True).exclude(off_id=""))
        if not off_foods:
            self.message_user(
                request,
                _("None of the selected foods were imported from OpenFoodFacts."),
                level=messages.WARNING,
            )
            return
        refreshed = 0
        for food in off_foods:
            previous_synced_at = food.off_synced_at
            result = services.import_or_refresh_food_from_off(food.off_id, force=True)
            if result is not None and result.off_synced_at != previous_synced_at:
                refreshed += 1
        self.message_user(
            request,
            _("Refreshed %(refreshed)d of %(total)d food(s) from OpenFoodFacts.")
            % {"refreshed": refreshed, "total": len(off_foods)},
        )

    @admin.action(description=_("Merge selected foods into one…"))
    def merge_selected_foods(self, request, queryset):
        if queryset.count() < 2:
            self.message_user(
                request,
                _("Select at least two foods to merge."),
                level=messages.WARNING,
            )
            return None
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        url = reverse("admin:nutrition_food_merge")
        return redirect(f"{url}?ids={ids}")

    def get_urls(self):
        custom_urls = [
            path(
                "merge/",
                self.admin_site.admin_view(self.merge_view),
                name="nutrition_food_merge",
            ),
        ]
        return custom_urls + super().get_urls()

    def merge_view(self, request):
        ids_param = request.POST.get("ids") if request.method == "POST" else request.GET.get(
            "ids", ""
        )
        food_ids = [int(pk) for pk in ids_param.split(",") if pk]
        foods = list(Food.objects.filter(pk__in=food_ids))
        changelist_url = reverse("admin:nutrition_food_changelist")
        if len(foods) < 2:
            self.message_user(
                request,
                _("Select at least two foods to merge."),
                level=messages.WARNING,
            )
            return redirect(changelist_url)

        if request.method == "POST":
            keep_id = int(request.POST["keep"])
            keep = next(food for food in foods if food.pk == keep_id)
            duplicates = [food for food in foods if food.pk != keep_id]
            services.merge_foods(keep, duplicates)
            self.message_user(
                request,
                _(
                    "Merged %(count)d food(s) into “%(name)s”. Every diary "
                    "entry, recipe ingredient, and diet-plan item that referenced "
                    "one of them now points at it instead."
                )
                % {"count": len(duplicates), "name": keep.name},
            )
            return redirect(changelist_url)

        context = {
            **self.admin_site.each_context(request),
            "title": _("Merge foods"),
            "opts": self.model._meta,
            "foods": foods,
            "ids": ids_param,
        }
        return render(request, "admin/nutrition/food/merge.html", context)


@admin.register(OpenFoodFactsSettings)
class OpenFoodFactsSettingsAdmin(admin.ModelAdmin):
    """Singleton — same pattern as apps.core.admin's
    BackupSettingsAdmin/FeedbackSettingsAdmin."""

    list_display = ["__str__", "enabled"]

    def has_add_permission(self, request):
        return not OpenFoodFactsSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 1


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "servings"]
    search_fields = ["name"]
    inlines = [RecipeIngredientInline]


@admin.register(DiaryEntry)
class DiaryEntryAdmin(admin.ModelAdmin):
    list_display = ["user", "date", "meal_slot", "food", "recipe", "quantity"]
    list_filter = ["meal_slot", "date"]
    search_fields = ["user__username"]


class DietPlanMealInline(admin.TabularInline):
    model = DietPlanMeal
    extra = 0


@admin.register(DietPlan)
class DietPlanAdmin(admin.ModelAdmin):
    list_display = ["user", "name", "target_calories", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["user__username", "name"]
    inlines = [DietPlanMealInline]


@admin.register(DietPlanItem)
class DietPlanItemAdmin(admin.ModelAdmin):
    list_display = ["diet_plan_meal", "food", "recipe", "quantity"]
