from django.contrib import admin

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
    list_display = ["name", "brand", "owner", "calories", "active"]
    list_filter = ["active"]
    search_fields = ["name", "brand"]


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
