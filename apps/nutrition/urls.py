from django.urls import path

from . import views

app_name = "nutrition"

urlpatterns = [
    path("", views.NutritionDashboardView.as_view(), name="dashboard"),
    path(
        "adjustment/accept/",
        views.accept_adjustment_suggestion,
        name="accept-adjustment-suggestion",
    ),
    path("onboarding/body/", views.OnboardingBodyView.as_view(), name="onboarding-body"),
    path(
        "onboarding/activity/",
        views.OnboardingActivityView.as_view(),
        name="onboarding-activity",
    ),
    path(
        "onboarding/activity-level/",
        views.OnboardingActivityLevelView.as_view(),
        name="onboarding-activity-level",
    ),
    path("onboarding/goal/", views.OnboardingGoalView.as_view(), name="onboarding-goal"),
    path("onboarding/review/", views.OnboardingReviewView.as_view(), name="onboarding-review"),
    path("foods/", views.FoodListView.as_view(), name="food-list"),
    path("foods/new/", views.FoodCreateView.as_view(), name="food-create"),
    path("foods/search/", views.FoodSearchResultsView.as_view(), name="food-search"),
    path("foods/browse/", views.FoodBrowseView.as_view(), name="food-browse"),
    path(
        "foods/browse/category/<str:category_id>/",
        views.FoodCategoryView.as_view(),
        name="food-category",
    ),
    path("foods/import/", views.food_import, name="food-import"),
    path("diary/", views.DiaryDayView.as_view(), name="diary-day"),
    # Must come before "diary/<str:target_date>/" below — Django tries
    # urlpatterns in order, and <str:target_date> matches any segment
    # including the literal "add", which would otherwise route POSTs
    # meant for DiaryAddEntryView into DiaryDayView (no post() method
    # there -> a 405 that looks like a routing failure, not what it
    # actually is: a routing *order* bug). Same class of pitfall as
    # apps.accounts.views.TwoFactorRegenerateBackupCodesView's earlier
    # 405, different root cause.
    path("diary/add/", views.DiaryAddEntryView.as_view(), name="diary-add-entry"),
    path("diary/entries/<int:pk>/edit/", views.diary_entry_edit, name="diary-entry-edit"),
    path("diary/entries/<int:pk>/delete/", views.diary_entry_delete, name="diary-entry-delete"),
    path("diary/<str:target_date>/", views.DiaryDayView.as_view(), name="diary-day"),
    path("diary/<str:source_date>/copy/", views.diary_day_copy, name="diary-day-copy"),
    path("stats/", views.NutritionStatsView.as_view(), name="stats"),
    path("recipes/", views.RecipeListView.as_view(), name="recipe-list"),
    path("recipes/new/", views.recipe_create, name="recipe-create"),
    path("recipes/<int:pk>/", views.RecipeDetailView.as_view(), name="recipe-detail"),
    path("recipes/<int:pk>/edit/", views.recipe_update, name="recipe-update"),
    path("recipes/<int:pk>/delete/", views.recipe_delete, name="recipe-delete"),
    path("recipes/<int:pk>/log/", views.recipe_log, name="recipe-log"),
    path(
        "recipes/<int:recipe_pk>/ingredients/new/",
        views.recipe_ingredient_create,
        name="recipe-ingredient-create",
    ),
    path(
        "recipes/<int:recipe_pk>/ingredients/<int:pk>/edit/",
        views.recipe_ingredient_edit,
        name="recipe-ingredient-edit",
    ),
    path(
        "recipes/<int:recipe_pk>/ingredients/<int:pk>/delete/",
        views.recipe_ingredient_delete,
        name="recipe-ingredient-delete",
    ),
    path("diet-plans/", views.DietPlanListView.as_view(), name="diet-plan-list"),
    path("diet-plans/new/", views.DietPlanCreateView.as_view(), name="diet-plan-create"),
    path(
        "diet-plans/<int:pk>/", views.DietPlanDetailView.as_view(), name="diet-plan-detail"
    ),
    path("diet-plans/<int:pk>/delete/", views.diet_plan_delete, name="diet-plan-delete"),
    path(
        "diet-plans/<int:pk>/toggle-active/",
        views.diet_plan_toggle_active,
        name="diet-plan-toggle-active",
    ),
    path("diet-plans/<int:pk>/log/", views.diet_plan_log, name="diet-plan-log"),
    path(
        "diet-plans/<int:plan_pk>/items/<int:pk>/edit/",
        views.diet_plan_item_edit,
        name="diet-plan-item-edit",
    ),
    path(
        "diet-plans/<int:plan_pk>/items/<int:pk>/delete/",
        views.diet_plan_item_delete,
        name="diet-plan-item-delete",
    ),
    path(
        "diet-plans/<int:plan_pk>/meals/<int:meal_pk>/items/new/",
        views.diet_plan_meal_item_add,
        name="diet-plan-meal-item-add",
    ),
    path("calculators/", views.CalculatorsHomeView.as_view(), name="calculators-home"),
    path(
        "calculators/bmr-tdee/",
        views.BmrTdeeCalculatorView.as_view(),
        name="calculator-bmr-tdee",
    ),
    path(
        "calculators/macros/", views.MacroCalculatorView.as_view(), name="calculator-macros"
    ),
    path(
        "calculators/body-fat/",
        views.BodyFatCalculatorView.as_view(),
        name="calculator-body-fat",
    ),
    path(
        "calculators/water-intake/",
        views.WaterIntakeCalculatorView.as_view(),
        name="calculator-water-intake",
    ),
    path("calculators/bmi/", views.BMICalculatorView.as_view(), name="calculator-bmi"),
    path(
        "calculators/waist-hip-ratio/",
        views.WaistHipRatioCalculatorView.as_view(),
        name="calculator-waist-hip-ratio",
    ),
    path(
        "calculators/time-to-goal/",
        views.TimeToGoalCalculatorView.as_view(),
        name="calculator-time-to-goal",
    ),
]
