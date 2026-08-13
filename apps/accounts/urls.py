from django.urls import path

from .views import ProfileView, SignupView

urlpatterns = [
    path("signup/", SignupView.as_view(), name="signup"),
    path("profile/", ProfileView.as_view(), name="profile"),
]
