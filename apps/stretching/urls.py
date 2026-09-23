from django.urls import path

from . import views

app_name = "stretching"

urlpatterns = [
    path("", views.StretchingHomeView.as_view(), name="home"),
    # Routines
    path("routines/", views.RoutineListView.as_view(), name="routine-list"),
    path("routines/new/", views.routine_create, name="routine-create"),
    path("routines/<int:pk>/", views.routine_detail, name="routine-detail"),
    path("routines/<int:pk>/edit/", views.routine_update, name="routine-update"),
    path("routines/<int:pk>/remove/", views.routine_deactivate, name="routine-deactivate"),
    path("routines/<int:pk>/copy/", views.routine_copy, name="routine-copy"),
    path("routines/<int:pk>/items/add/", views.routine_item_add, name="routine-item-add"),
    path(
        "routines/<int:routine_pk>/items/<int:pk>/edit/",
        views.routine_item_update,
        name="routine-item-update",
    ),
    path(
        "routines/<int:routine_pk>/items/<int:pk>/delete/",
        views.routine_item_delete,
        name="routine-item-delete",
    ),
    path(
        "routines/<int:routine_pk>/items/<int:pk>/move/",
        views.routine_item_move,
        name="routine-item-move",
    ),
    # Stretch library
    path("library/", views.stretch_list, name="stretch-list"),
    path("library/new/", views.stretch_create, name="stretch-create"),
    path("library/<int:pk>/", views.stretch_detail, name="stretch-detail"),
    path("library/<int:pk>/edit/", views.stretch_update, name="stretch-update"),
    path("library/<int:pk>/remove/", views.stretch_deactivate, name="stretch-deactivate"),
    # Sessions
    path("sessions/", views.session_history, name="session-history"),
    path("sessions/log/", views.quick_log, name="quick-log"),
    path("sessions/start/<int:routine_pk>/", views.session_start, name="session-start"),
    path(
        "sessions/cool-down/<int:workout_session_pk>/",
        views.session_start_cooldown,
        name="session-start-cooldown",
    ),
    path("sessions/<int:pk>/", views.session_detail, name="session-detail"),
    path("sessions/<int:pk>/play/", views.session_play, name="session-play"),
    path(
        "sessions/<int:pk>/stretches/<int:performed_pk>/mark/",
        views.performed_mark,
        name="performed-mark",
    ),
    path("sessions/<int:pk>/complete/", views.session_complete, name="session-complete"),
    path("sessions/<int:pk>/abandon/", views.session_abandon, name="session-abandon"),
    path("sessions/<int:pk>/edit/", views.session_edit, name="session-edit"),
    path("sessions/<int:pk>/delete/", views.session_delete, name="session-delete"),
]
