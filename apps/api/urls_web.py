from django.urls import path

from . import views_web

app_name = "api_keys"

urlpatterns = [
    path("", views_web.key_list, name="key-list"),
    path("new/", views_web.key_create, name="key-create"),
    path("<int:pk>/created/", views_web.key_created, name="key-created"),
    path("<int:pk>/revoke/", views_web.key_revoke, name="key-revoke"),
]
