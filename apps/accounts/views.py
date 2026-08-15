from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, PasswordResetView
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, UpdateView

from apps.core import changelog as changelog_services
from apps.core.models import FeedbackSettings

from .forms import (
    AccountDetailsForm,
    ProfileForm,
    RateLimitedAuthenticationForm,
    RateLimitedPasswordResetForm,
    SignupForm,
)


class SignupView(CreateView):
    form_class = SignupForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("dashboard")

    def dispatch(self, request, *args, **kwargs):
        # docs/SECURITY.md — a self-hosted instance isn't necessarily
        # meant to accept public registration; DJANGO_SIGNUP_ENABLED
        # gates this URL directly rather than only hiding its link on
        # the login page, since a hidden link doesn't stop someone who
        # already knows/guesses the path.
        if not settings.SIGNUP_ENABLED:
            messages.info(request, _("Registration is currently closed."))
            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response


class RateLimitedLoginView(LoginView):
    """The bare `django.contrib.auth.urls` login view has no brute-
    force protection at all — apps.api's rate limiting is a completely
    separate, API-key-only mechanism. See
    apps.accounts.forms.RateLimitedAuthenticationForm for the actual
    limiting; this subclass exists to plug that form in and to expose
    SIGNUP_ENABLED so the template can hide the "create an account"
    link when registration is closed."""

    authentication_form = RateLimitedAuthenticationForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["signup_enabled"] = settings.SIGNUP_ENABLED
        return context


class RateLimitedPasswordResetView(PasswordResetView):
    """See apps.accounts.forms.RateLimitedPasswordResetForm for the
    actual limiting; this subclass exists to plug that form in and to
    give it the `request` it needs (PasswordResetForm doesn't accept
    that kwarg itself the way AuthenticationForm does)."""

    form_class = RateLimitedPasswordResetForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs


class ProfileView(LoginRequiredMixin, UpdateView):
    form_class = ProfileForm
    template_name = "accounts/profile.html"
    success_url = reverse_lazy("profile")

    def get_object(self):
        return self.request.user

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Powers the version-number modal (docs/ARCHITECTURE.md
        # "Versioning") — cached in apps.core.changelog, so this is a
        # cheap lookup, not a re-parse of CHANGELOG.md on every load.
        context["changelog_html"] = changelog_services.render_changelog_html()
        # Hides the "Feedback" card below when submissions are closed —
        # apps.core.views_feedback.FeedbackCreateView enforces the same
        # setting itself, so this is just avoiding a dead link, not the
        # actual gate.
        context["feedback_enabled"] = FeedbackSettings.load().enabled
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Preferences saved."))
        return response


class AccountDetailsView(LoginRequiredMixin, UpdateView):
    """Username/name/email — kept as its own page rather than folded
    into ProfileView's preferences form, next to "Change password" on
    the profile page: both are account-identity actions distinct from
    display preferences, and both already redirect straight back to
    that page's own toast rather than a separate confirmation page."""

    form_class = AccountDetailsForm
    template_name = "accounts/account_details_form.html"
    success_url = reverse_lazy("account-details")

    def get_object(self):
        return self.request.user

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Account details saved."))
        return response
