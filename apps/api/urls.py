from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views, views_coaching, views_social, views_tutorials

app_name = "api"



class ApiRouter(DefaultRouter):
    """Every row id in this API is an integer, so the router's detail
    URLs only match digits - which also gives the OpenAPI schema an
    integer `id` parameter instead of a guessed string (the viewsets'
    querysets depend on the request's user, so drf-spectacular can't
    derive the type from the model itself)."""

    def __init__(self):
        super().__init__(use_regex_path=False)
        self._default_value_pattern = "int"


router = ApiRouter()
router.register("exercises", views.ExerciseViewSet, basename="exercise")
router.register("muscle-groups", views.MuscleGroupViewSet, basename="muscle-group")
router.register("equipment", views.EquipmentViewSet, basename="equipment")
router.register("programs", views.ProgramViewSet, basename="program")
router.register("workouts", views.WorkoutViewSet, basename="workout")
router.register("prescriptions", views.ExercisePrescriptionViewSet, basename="prescription")
router.register("sessions", views.WorkoutSessionViewSet, basename="session")
router.register(
    "performed-exercises", views.PerformedExerciseViewSet, basename="performed-exercise"
)
router.register("sets", views.ExerciseSetViewSet, basename="set")
router.register("measurement-types", views.MeasurementTypeViewSet, basename="measurement-type")
router.register("measurements", views.BodyMeasurementViewSet, basename="measurement")
router.register("activity-types", views.ActivityTypeViewSet, basename="activity-type")
router.register("activities", views.ActivityViewSet, basename="activity")
router.register("records", views.PersonalRecordViewSet, basename="record")
router.register("foods", views.FoodViewSet, basename="food")
router.register("meal-slots", views.MealSlotViewSet, basename="meal-slot")
router.register("recipes", views.RecipeViewSet, basename="recipe")
router.register(
    "recipe-ingredients", views.RecipeIngredientViewSet, basename="recipe-ingredient"
)
router.register("diary-entries", views.DiaryEntryViewSet, basename="diary-entry")
router.register("nutrition-goals", views.NutritionGoalViewSet, basename="nutrition-goal")
router.register("nutrition-targets", views.NutritionTargetViewSet, basename="nutrition-target")
router.register("diet-plans", views.DietPlanViewSet, basename="diet-plan")
router.register("diet-plan-meals", views.DietPlanMealViewSet, basename="diet-plan-meal")
router.register("diet-plan-items", views.DietPlanItemViewSet, basename="diet-plan-item")
router.register("stretches", views.StretchViewSet, basename="stretch")
router.register("stretch-routines", views.StretchRoutineViewSet, basename="stretch-routine")
router.register("stretch-routine-items", views.RoutineItemViewSet, basename="stretch-routine-item")
router.register("stretch-sessions", views.StretchSessionViewSet, basename="stretch-session")

_social = views_social
social_urlpatterns = [
    path("social/friends/", _social.FriendListView.as_view(), name="social-friends"),
    path(
        "social/friends/<int:user_id>/",
        _social.FriendDetailView.as_view(),
        name="social-friend",
    ),
    path(
        "social/friends/<int:user_id>/mute/",
        _social.FriendMuteView.as_view(mute=True),
        name="social-friend-mute",
    ),
    path(
        "social/friends/<int:user_id>/unmute/",
        _social.FriendMuteView.as_view(mute=False),
        name="social-friend-unmute",
    ),
    path("social/users/", _social.UserSearchView.as_view(), name="social-users"),
    path(
        "social/friend-requests/",
        _social.FriendRequestListView.as_view(),
        name="social-friend-requests",
    ),
    path(
        "social/friend-requests/<int:pk>/accept/",
        _social.FriendRequestRespondView.as_view(accept=True),
        name="social-friend-request-accept",
    ),
    path(
        "social/friend-requests/<int:pk>/decline/",
        _social.FriendRequestRespondView.as_view(accept=False),
        name="social-friend-request-decline",
    ),
    path("social/blocks/", _social.BlockListView.as_view(), name="social-blocks"),
    path(
        "social/blocks/<int:user_id>/", _social.BlockDetailView.as_view(), name="social-block"
    ),
    path(
        "social/conversations/",
        _social.ConversationListView.as_view(),
        name="social-conversations",
    ),
    path(
        "social/messages/<int:user_id>/",
        _social.DirectMessageView.as_view(),
        name="social-messages",
    ),
    path("social/groups/", _social.GroupListView.as_view(), name="social-groups"),
    path("social/groups/join/", _social.GroupJoinView.as_view(), name="social-group-join"),
    path("social/groups/<int:pk>/", _social.GroupDetailView.as_view(), name="social-group"),
    path(
        "social/groups/<int:pk>/members/",
        _social.GroupMemberListView.as_view(),
        name="social-group-members",
    ),
    path(
        "social/groups/<int:pk>/members/<int:user_id>/",
        _social.GroupMemberView.as_view(),
        name="social-group-member",
    ),
    path(
        "social/groups/<int:pk>/transfer/",
        _social.GroupTransferView.as_view(),
        name="social-group-transfer",
    ),
    path(
        "social/groups/<int:pk>/leave/",
        _social.GroupLeaveView.as_view(),
        name="social-group-leave",
    ),
    path(
        "social/groups/<int:pk>/mute/", _social.GroupMuteView.as_view(), name="social-group-mute"
    ),
    path(
        "social/groups/<int:pk>/invite-link/",
        _social.GroupInviteLinkView.as_view(),
        name="social-group-invite-link",
    ),
    path(
        "social/groups/<int:pk>/invites/",
        _social.GroupInviteSendView.as_view(),
        name="social-group-invites",
    ),
    path(
        "social/groups/<int:pk>/messages/",
        _social.GroupMessageView.as_view(),
        name="social-group-messages",
    ),
    path(
        "social/group-invites/",
        _social.GroupInviteListView.as_view(),
        name="social-group-invite-list",
    ),
    path(
        "social/group-invites/<int:pk>/accept/",
        _social.GroupInviteRespondView.as_view(accept=True),
        name="social-group-invite-accept",
    ),
    path(
        "social/group-invites/<int:pk>/decline/",
        _social.GroupInviteRespondView.as_view(accept=False),
        name="social-group-invite-decline",
    ),
]

_coaching = views_coaching
coaching_urlpatterns = [
    path(
        "coaching/requests/",
        _coaching.CoachingRequestListView.as_view(),
        name="coaching-requests",
    ),
    path(
        "coaching/requests/<int:pk>/accept/",
        _coaching.CoachingRequestRespondView.as_view(accept=True),
        name="coaching-request-accept",
    ),
    path(
        "coaching/requests/<int:pk>/decline/",
        _coaching.CoachingRequestRespondView.as_view(accept=False),
        name="coaching-request-decline",
    ),
    path("coaching/coaches/", _coaching.CoachListView.as_view(), name="coaching-coaches"),
    path("coaching/clients/", _coaching.ClientListView.as_view(), name="coaching-clients"),
    path(
        "coaching/clients/<str:username>/",
        _coaching.ClientDetailView.as_view(),
        name="coaching-client",
    ),
    path(
        "coaching/relationships/<int:pk>/end/",
        _coaching.RelationshipEndView.as_view(),
        name="coaching-relationship-end",
    ),
    path("coaching/shared/", _coaching.SharedPlanListView.as_view(), name="coaching-shared"),
    path(
        "coaching/shared/programs/<int:pk>/import/",
        _coaching.SharedProgramImportView.as_view(),
        name="coaching-shared-program-import",
    ),
    path(
        "coaching/shared/diet-plans/<int:pk>/import/",
        _coaching.SharedDietPlanImportView.as_view(),
        name="coaching-shared-diet-plan-import",
    ),
    path(
        "coaching/programs/<int:pk>/update/",
        _coaching.ProgramCoachUpdateView.as_view(),
        name="coaching-program-update",
    ),
    path(
        "coaching/diet-plans/<int:pk>/update/",
        _coaching.DietPlanCoachUpdateView.as_view(),
        name="coaching-diet-plan-update",
    ),
    path(
        "coaching/programs/<int:pk>/clients/",
        _coaching.ProgramShareView.as_view(),
        name="coaching-program-clients",
    ),
    path(
        "coaching/diet-plans/<int:pk>/clients/",
        _coaching.DietPlanShareView.as_view(),
        name="coaching-diet-plan-clients",
    ),
]

urlpatterns = [
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("tutorials/", views_tutorials.TutorialListView.as_view(), name="tutorial-list"),
    path(
        "tutorials/reset/",
        views_tutorials.TutorialResetAllView.as_view(),
        name="tutorial-reset-all",
    ),
    path(
        "tutorials/<str:key>/complete/",
        views_tutorials.TutorialCompleteView.as_view(),
        name="tutorial-complete",
    ),
    path(
        "tutorials/<str:key>/reset/",
        views_tutorials.TutorialResetView.as_view(),
        name="tutorial-reset",
    ),
    path("nutrition/profile/", views.NutritionProfileView.as_view(), name="nutrition-profile"),
    path(
        "nutrition/daily-totals/", views.DailyTotalsView.as_view(), name="nutrition-daily-totals"
    ),
    path(
        "nutrition/adjustment-suggestion/",
        views.AdjustmentSuggestionView.as_view(),
        name="nutrition-adjustment-suggestion",
    ),
    path(
        "nutrition/adjustment-suggestion/accept/",
        views.AdjustmentSuggestionAcceptView.as_view(),
        name="nutrition-adjustment-suggestion-accept",
    ),
    path("up-next/", views.UpNextView.as_view(), name="up-next"),
    path(
        "exercises/<int:pk>/last-performance/",
        views.LastPerformanceView.as_view(),
        name="exercise-last-performance",
    ),
    path("analytics/summary/", views.TrainingSummaryView.as_view(), name="analytics-summary"),
    path("analytics/volume/", views.VolumeAnalyticsView.as_view(), name="analytics-volume"),
    path(
        "analytics/muscle-groups/",
        views.MuscleGroupAnalyticsView.as_view(),
        name="analytics-muscle-groups",
    ),
    path(
        "analytics/exercises/<int:pk>/",
        views.ExerciseAnalyticsView.as_view(),
        name="analytics-exercise",
    ),
    path(
        "analytics/recently-active/",
        views.RecentlyActiveView.as_view(),
        name="analytics-recently-active",
    ),
    path(
        "analytics/achievements/",
        views.AchievementsView.as_view(),
        name="analytics-achievements",
    ),
] + social_urlpatterns + coaching_urlpatterns + router.urls
