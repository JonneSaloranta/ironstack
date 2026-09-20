from django.urls import path

from . import views_analytics, views_plans, views_requests

app_name = "coaching"

urlpatterns = [
    path("clients/", views_analytics.ClientListView.as_view(), name="client-list"),
    path(
        "clients/<str:username>/",
        views_analytics.ClientDetailView.as_view(),
        name="client-detail",
    ),
    path("my-coaches/", views_requests.my_coaches, name="my-coaches"),
    path(
        "request/<int:user_id>/", views_requests.coaching_request_send, name="request-send"
    ),
    path(
        "requests/<int:pk>/respond/",
        views_requests.coaching_request_respond,
        name="request-respond",
    ),
    path(
        "relationships/<int:pk>/end/",
        views_requests.coaching_relationship_end,
        name="relationship-end",
    ),
    path("plans/", views_plans.shared_plan_list, name="shared-plan-list"),
    path(
        "plans/programs/<int:pk>/import/",
        views_plans.shared_program_import,
        name="program-import",
    ),
    path(
        "plans/programs/<int:pk>/update/",
        views_plans.program_update_preview,
        name="program-update-preview",
    ),
    path(
        "plans/programs/<int:pk>/update/apply/",
        views_plans.program_update_apply,
        name="program-update-apply",
    ),
    path(
        "plans/diet-plans/<int:pk>/import/",
        views_plans.shared_diet_plan_import,
        name="diet-plan-import",
    ),
    path(
        "plans/diet-plans/<int:pk>/update/",
        views_plans.diet_plan_update_preview,
        name="diet-plan-update-preview",
    ),
    path(
        "plans/diet-plans/<int:pk>/update/apply/",
        views_plans.diet_plan_update_apply,
        name="diet-plan-update-apply",
    ),
]
