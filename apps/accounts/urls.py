from django.urls import path

from .views import AccountDetailsView, ProfileView, SignupView

urlpatterns = [
    path("signup/", SignupView.as_view(), name="signup"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("account-details/", AccountDetailsView.as_view(), name="account-details"),
]
