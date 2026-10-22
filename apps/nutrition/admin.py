import math
from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from . import off_refresh_worker, services
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


class OffSyncStatusFilter(admin.SimpleListFilter):
    """Finds the OpenFoodFacts foods that need a refresh, so "Stale" +
    "select all" + one action refreshes every one of them."""

    title = _("OpenFoodFacts sync")
    parameter_name = "off_sync"

    def lookups(self, request, model_admin):
        return [
            ("never", _("Never synced")),
            ("stale", _("Stale")),
            ("fresh", _("Fresh")),
            ("queued", _("Queued for refresh")),
        ]

    def queryset(self, request, queryset):
        cutoff = timezone.now() - timedelta(days=services.OPENFOODFACTS_STALENESS_DAYS)
        off_foods = queryset.exclude(off_id__isnull=True).exclude(off_id="")
        match self.value():
            case "never":
                return off_foods.filter(off_synced_at__isnull=True)
            case "stale":
                return off_foods.filter(off_synced_at__lte=cutoff)
            case "fresh":
                return off_foods.filter(off_synced_at__gt=cutoff)
            case "queued":
                return queryset.filter(
                    Q(off_refresh_requested_at__isnull=False)
                    | Q(price_refresh_requested_at__isnull=False)
                )
        return queryset


@admin.register(Food)
class FoodAdmin(admin.ModelAdmin):
    """Two admin-only bulk actions, both asked for directly:

    `merge_selected_foods` - deleting a duplicate outright cascades
    away every DiaryEntry/RecipeIngredient/DietPlanItem that ever
    referenced it, which is exactly the kind of silent history loss
    CLAUDE.md warns against - re-points every reference from the
    duplicates onto one kept row instead, see
    apps.nutrition.services.merge_foods's own docstring. Django's
    default "Delete selected foods" action is left as-is (still
    reachable for a genuinely bogus row nothing references) - its own
    built-in confirmation page already lists every object that would
    cascade-delete before an admin confirms, real standard protection
    this feature doesn't need to duplicate.

    `refresh_selected_from_off` - force-refreshes selected foods from
    OpenFoodFacts rather than waiting for the normal 14-day staleness
    gate to trigger on next use. Still an explicit, admin-chosen
    selection, not the unconditional bulk re-sync this integration was
    deliberately scoped away from in the first place
    (docs/NUTRITION.md "OpenFoodFacts integration"). The selection is
    queued and refreshed in the background at a rate-limit-safe pace
    (apps.nutrition.off_refresh_worker), so it can be any size.

    `refresh_selected_prices` - the same shape, for Open Prices
    (docs/NUTRITION.md "Open Prices integration") instead of OFF's
    core product API: force-refreshes selected foods' cached
    `price_amount`/`price_currency` immediately rather than waiting
    for the normal 7-day staleness gate."""

    list_display = [
        "name", "brand", "owner", "calories", "nutri_score", "nova_group", "off_id",
        "off_synced_at", "price_synced_at", "active",
    ]
    list_filter = [OffSyncStatusFilter, "active", "nutri_score", "nova_group"]
    search_fields = ["name", "brand", "off_id"]
    actions = [
        "merge_selected_foods",
        "refresh_selected_from_off",
        "refresh_selected_prices",
        "cancel_queued_refresh",
    ]

    def changelist_view(self, request, extra_context=None):
        pending = services.pending_off_refresh_count()
        if pending and request.method == "GET":
            if off_refresh_worker.is_running():
                text = ngettext(
                    "%(count)d food is queued for an OpenFoodFacts refresh and is being "
                    "refreshed in the background.",
                    "%(count)d foods are queued for an OpenFoodFacts refresh and are being "
                    "refreshed in the background.",
                    pending,
                )
            else:
                text = ngettext(
                    "%(count)d food is queued for an OpenFoodFacts refresh, but the "
                    "background refresh isn't running - run a refresh action again to "
                    "restart it.",
                    "%(count)d foods are queued for an OpenFoodFacts refresh, but the "
                    "background refresh isn't running - run a refresh action again to "
                    "restart it.",
                    pending,
                )
            self.message_user(request, text % {"count": pending}, level=messages.INFO)
        return super().changelist_view(request, extra_context)

    @admin.action(description=_("Refresh selected foods from OpenFoodFacts"))
    def refresh_selected_from_off(self, request, queryset):
        # Queued, not refreshed here: a request can only spend one
        # minute of Open Food Facts' read budget, while the background
        # worker (apps.nutrition.off_refresh_worker) paces any number
        # of foods. force=True there bypasses the 14-day staleness gate
        # - an explicit admin request for these rows, not the
        # unconditional bulk re-sync this integration was deliberately
        # scoped away from (docs/NUTRITION.md "OpenFoodFacts integration").
        self._queue_refresh(request, queryset, prices=False)

    @admin.action(description=_("Refresh selected foods' prices from Open Prices"))
    def refresh_selected_prices(self, request, queryset):
        # Same queue and worker as refresh_selected_from_off, for Open
        # Prices (docs/NUTRITION.md "Open Prices integration").
        self._queue_refresh(request, queryset, prices=True)

    @admin.action(description=_("Cancel queued OpenFoodFacts refresh"))
    def cancel_queued_refresh(self, request, queryset):
        cancelled = services.cancel_off_refresh(queryset)
        self.message_user(
            request,
            ngettext(
                "Removed %(count)d food from the refresh queue.",
                "Removed %(count)d foods from the refresh queue.",
                cancelled,
            )
            % {"count": cancelled},
        )

    def _queue_refresh(self, request, queryset, *, prices):
        if not queryset.exclude(off_id__isnull=True).exclude(off_id="").exists():
            self.message_user(
                request,
                _("None of the selected foods were imported from OpenFoodFacts."),
                level=messages.WARNING,
            )
            return
        queued = services.queue_off_refresh(queryset, prices=prices)
        pending = services.pending_off_refresh_count()
        self.message_user(
            request,
            ngettext(
                "Queued %(queued)d food for a background refresh.",
                "Queued %(queued)d foods for a background refresh.",
                queued,
            )
            % {"queued": queued}
            + " "
            + _(
                "To stay within Open Food Facts' rate limit about %(rate)d are refreshed "
                "a minute, so the %(pending)d still queued take about %(minutes)d min."
            )
            % {
                "rate": off_refresh_worker.WORKER_READS_PER_MINUTE,
                "pending": pending,
                "minutes": math.ceil(pending / off_refresh_worker.WORKER_READS_PER_MINUTE),
            },
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
    """Singleton - same pattern as apps.core.admin's
    BackupSettingsAdmin/FeedbackSettingsAdmin."""

    list_display = ["__str__", "enabled", "contact_email"]

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
    list_display = ["user", "date", "meal_slot", "food", "recipe", "quick_name", "quantity"]
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
