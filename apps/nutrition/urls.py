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
]
