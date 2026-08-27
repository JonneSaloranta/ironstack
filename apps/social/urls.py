from django.urls import path

from . import views_friends, views_groups, views_messages

app_name = "social"

urlpatterns = [
    # Friends
    path("friends/", views_friends.friend_list, name="friend-list"),
    path("friends/search/", views_friends.friend_search, name="friend-search"),
    path(
        "friends/<int:user_id>/request/",
        views_friends.friend_request_send,
        name="friend-request-send",
    ),
    path(
        "friends/requests/<int:pk>/respond/",
        views_friends.friend_request_respond,
        name="friend-request-respond",
    ),
    path("friends/<int:user_id>/remove/", views_friends.friend_remove, name="friend-remove"),
    # Blocking
    path("blocked/", views_friends.block_list, name="block-list"),
    path("blocked/<int:user_id>/block/", views_friends.block_user_view, name="block-user"),
    path("blocked/<int:user_id>/unblock/", views_friends.unblock_user_view, name="unblock-user"),
    # Groups
    path("groups/", views_groups.group_list, name="group-list"),
    path("groups/new/", views_groups.group_create, name="group-create"),
    path("groups/<int:pk>/", views_groups.group_detail, name="group-detail"),
    path("groups/<int:pk>/edit/", views_groups.group_edit, name="group-edit"),
    path("groups/<int:pk>/delete/", views_groups.group_delete, name="group-delete"),
    path("groups/<int:pk>/leave/", views_groups.group_leave, name="group-leave"),
    path(
        "groups/<int:pk>/invite-link/toggle/",
        views_groups.group_invite_toggle,
        name="group-invite-toggle",
    ),
    path(
        "groups/<int:pk>/invite-link/regenerate/",
        views_groups.group_invite_regenerate,
        name="group-invite-regenerate",
    ),
    path(
        "groups/<int:pk>/invite/send/", views_groups.group_invite_send, name="group-invite-send"
    ),
    path(
        "groups/invites/<int:pk>/respond/",
        views_groups.group_invite_respond,
        name="group-invite-respond",
    ),
    path(
        "groups/<int:pk>/members/<int:user_id>/remove/",
        views_groups.group_member_remove,
        name="group-member-remove",
    ),
    path(
        "groups/<int:pk>/members/<int:user_id>/role/",
        views_groups.group_member_role,
        name="group-member-role",
    ),
    path(
        "groups/<int:pk>/members/<int:user_id>/transfer-ownership/",
        views_groups.group_transfer_ownership,
        name="group-transfer-ownership",
    ),
    # Messages
    path("", views_messages.message_list, name="message-list"),
    path("messages/<int:user_id>/", views_messages.message_thread, name="message-thread"),
    path(
        "messages/<int:user_id>/fragment/",
        views_messages.message_thread_fragment,
        name="message-thread-fragment",
    ),
    path("groups/<int:pk>/chat/", views_messages.group_thread, name="group-thread"),
    path(
        "groups/<int:pk>/chat/fragment/",
        views_messages.group_thread_fragment,
        name="group-thread-fragment",
    ),
]
