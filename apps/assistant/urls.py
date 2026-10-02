from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("", views.home, name="home"),
    path("new/", views.conversation_create, name="conversation-create"),
    path("conversations/<int:pk>/", views.conversation_detail, name="conversation-detail"),
    path("conversations/<int:pk>/send/", views.message_send, name="message-send"),
    path("conversations/<int:pk>/delete/", views.conversation_delete, name="conversation-delete"),
    path("messages/<int:pk>/", views.message_fragment, name="message-fragment"),
    path("proposals/<int:pk>/accept/", views.proposal_accept, name="proposal-accept"),
    path("proposals/<int:pk>/dismiss/", views.proposal_dismiss, name="proposal-dismiss"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/enable/", views.enable, name="enable"),
    path("settings/disable/", views.disable, name="disable"),
    path("settings/key/", views.key_save, name="key-save"),
    path("settings/key/remove/", views.key_remove, name="key-remove"),
    path("admin/", views.AdminSettingsView.as_view(), name="admin-settings"),
]
