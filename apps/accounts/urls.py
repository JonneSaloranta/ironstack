from django.urls import path

from .views import (
    AccountDetailsView,
    ProfileView,
    SignupView,
    TwoFactorDisableView,
    TwoFactorManageView,
    TwoFactorRegenerateBackupCodesView,
    TwoFactorSetupView,
    TwoFactorVerifyView,
)

urlpatterns = [
    path("signup/", SignupView.as_view(), name="signup"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("account-details/", AccountDetailsView.as_view(), name="account-details"),
    path("two-factor/setup/", TwoFactorSetupView.as_view(), name="two-factor-setup"),
    path("two-factor/manage/", TwoFactorManageView.as_view(), name="two-factor-manage"),
    path("two-factor/disable/", TwoFactorDisableView.as_view(), name="two-factor-disable"),
    path(
        "two-factor/backup-codes/regenerate/",
        TwoFactorRegenerateBackupCodesView.as_view(),
        name="two-factor-regenerate-backup-codes",
    ),
    # Not under login_required — the user isn't authenticated yet at
    # this point (see TwoFactorVerifyView's own docstring). No naming
    # collision with django.contrib.auth.urls to worry about here
    # (unlike login/password_reset above in config.urls), so this can
    # live in this app's own urls.py rather than needing to be
    # registered ahead of that include().
    path("two-factor/verify/", TwoFactorVerifyView.as_view(), name="two-factor-verify"),
]
