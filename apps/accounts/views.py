from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, UpdateView

from apps.core import changelog as changelog_services

from .forms import AccountDetailsForm, ProfileForm, SignupForm


class SignupView(CreateView):
    form_class = SignupForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response


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
