from django.urls import path

from . import views

app_name = "nutrition"

urlpatterns = [
    path("", views.NutritionDashboardView.as_view(), name="dashboard"),
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
]
