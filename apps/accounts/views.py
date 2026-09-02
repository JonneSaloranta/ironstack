import csv
import io
import json
import zipfile

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, PasswordResetView
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, FormView, TemplateView, UpdateView, View

from apps.core import changelog as changelog_services
from apps.core.models import FeedbackSettings

from . import services as account_services
from . import twofactor
from .forms import (
    AccountDeleteForm,
    AccountDetailsForm,
    OnboardingForm,
    ProfileForm,
    RateLimitedAuthenticationForm,
    RateLimitedPasswordResetForm,
    SignupForm,
    TwoFactorDisableForm,
    TwoFactorSetupConfirmForm,
    TwoFactorVerifyForm,
)
from .models import SiteDisclaimer


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
        # A local-password signup makes no sense once local password
        # login itself is off (docs/SECURITY.md "Single sign-on
        # (Authentik / OIDC)") — same reasoning and same direct URL
        # gate as SIGNUP_ENABLED above, not just hiding the link.
        if not settings.PASSWORD_LOGIN_ENABLED:
            messages.info(request, _("Registration is currently closed."))
            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["disclaimer_text"] = SiteDisclaimer.load().text
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        # Explicit `backend=`: form.save() creates `self.object` with a
        # plain ORM insert, never through authenticate(), so it has no
        # `.backend` attribute for login() to read. That's normally
        # fine — login() infers it on its own when exactly one backend
        # is configured — but breaks the moment Authentik SSO is also
        # enabled (settings.AUTHENTIK_ENABLED, config.settings.base's
        # AUTHENTICATION_BACKENDS) and there are two. A local-password
        # signup is unambiguously a ModelBackend account regardless.
        login(self.request, self.object, backend="django.contrib.auth.backends.ModelBackend")
        return response


class RateLimitedLoginView(LoginView):
    """The bare `django.contrib.auth.urls` login view has no brute-
    force protection at all — apps.api's rate limiting is a completely
    separate, API-key-only mechanism. See
    apps.accounts.forms.RateLimitedAuthenticationForm for the actual
    limiting; this subclass exists to plug that form in, to expose
    SIGNUP_ENABLED/the site disclaimer to the template, and to detour
    through a second factor (form_valid below) for a user who's
    enabled 2FA."""

    authentication_form = RateLimitedAuthenticationForm

    def dispatch(self, request, *args, **kwargs):
        # Blocks the POST channel directly, not just the template's
        # own `{% if password_login_enabled %}` — see docs/SECURITY.md
        # "Single sign-on (Authentik / OIDC)". GET still renders
        # normally (the page itself, minus the password form, still
        # needs to show the "Log in with Authentik" button and the
        # site disclaimer).
        if request.method == "POST" and not settings.PASSWORD_LOGIN_ENABLED:
            messages.info(request, _("Password login is disabled on this instance."))
            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["signup_enabled"] = settings.SIGNUP_ENABLED
        context["password_login_enabled"] = settings.PASSWORD_LOGIN_ENABLED
        context["authentik_enabled"] = settings.AUTHENTIK_ENABLED
        context["disclaimer_text"] = SiteDisclaimer.load().text
        return context

    def form_valid(self, form):
        # Username/password already checked correct at this point
        # (AuthenticationForm.clean already ran) — django.contrib.auth.
        # views.LoginView's own form_valid would log the user in
        # immediately here. A user with 2FA enabled instead gets
        # parked one step short of that: their id goes into the
        # session under a key that only TwoFactorVerifyView reads, and
        # nothing calls login() (so request.user stays anonymous, no
        # session-fixation-relevant state changes yet) until they
        # actually submit a correct code there.
        user = form.get_user()
        if user.totp_enabled:
            self.request.session["pre_2fa_user_id"] = user.pk
            next_url = self.get_success_url()
            return redirect(f"{reverse('two-factor-verify')}?next={next_url}")
        return super().form_valid(form)


class RateLimitedPasswordResetView(PasswordResetView):
    """See apps.accounts.forms.RateLimitedPasswordResetForm for the
    actual limiting; this subclass exists to plug that form in and to
    give it the `request` it needs (PasswordResetForm doesn't accept
    that kwarg itself the way AuthenticationForm does)."""

    form_class = RateLimitedPasswordResetForm

    def dispatch(self, request, *args, **kwargs):
        # A password reset is meaningless once local password login
        # itself is off — same gate and reasoning as SignupView's own
        # (docs/SECURITY.md "Single sign-on (Authentik / OIDC)").
        if not settings.PASSWORD_LOGIN_ENABLED:
            messages.info(request, _("Password login is disabled on this instance."))
            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

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
        # ?changelog=1 opens the same modal straight away — the click
        # target apps.core.management.commands.announce_version_update's
        # push notification uses (a Web Push notification can only ever
        # open a URL, not call into Alpine state directly), same
        # ?welcome=1-on-load pattern TwoFactorBackupCodesView already
        # uses for its own "just did the thing that led here" case.
        context["open_changelog"] = self.request.GET.get("changelog") == "1"
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


class TwoFactorVerifyView(FormView):
    """The login flow's second step, reached only via
    RateLimitedLoginView.form_valid's redirect once a correct password
    was already entered for a user with 2FA enabled. Deliberately not
    LoginRequiredMixin — the user isn't authenticated yet at this
    point, that's the whole reason this view exists."""

    template_name = "registration/two_factor_verify.html"
    form_class = TwoFactorVerifyForm

    def dispatch(self, request, *args, **kwargs):
        # No pending login to complete (a direct visit, an already-
        # completed/expired flow, ...) — nothing to verify a code
        # against, so back to the start.
        if "pre_2fa_user_id" not in request.session:
            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_user(self):
        User = get_user_model()
        try:
            return User.objects.get(pk=self.request.session["pre_2fa_user_id"])
        except User.DoesNotExist:
            raise Http404 from None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.get_user()
        return kwargs

    def form_valid(self, form):
        user = self.get_user()
        del self.request.session["pre_2fa_user_id"]
        # Explicit `backend=`: this user was fetched with a plain
        # User.objects.get() (get_user() above), never through
        # authenticate(), so it has no `.backend` attribute — same gap,
        # same fix, and same reasoning as SignupView.form_valid's own
        # explicit backend= (see that one's comment). The password that
        # got this session to `pre_2fa_user_id` in the first place was
        # already checked by RateLimitedLoginView's ModelBackend-only
        # AuthenticationForm, so this step is unambiguously ModelBackend
        # too — an SSO login never reaches this 2FA-verify view at all
        # (see this class's own docstring/"2FA note" in docs/SECURITY.md).
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        next_url = self.request.GET.get("next") or settings.LOGIN_REDIRECT_URL
        return redirect(next_url)


class TwoFactorManageView(LoginRequiredMixin, TemplateView):
    """Profile → Two-factor authentication → "Manage", once already
    enabled — consolidates "regenerate backup codes" and "disable" in
    one place, the same way Profile itself only ever links out to a
    dedicated page for anything with more than one simple action
    (Account details, API keys, ...) rather than crowding several
    buttons onto the profile card itself."""

    template_name = "accounts/two_factor_manage.html"

    def dispatch(self, request, *args, **kwargs):
        # Must check is_authenticated before touching totp_enabled at
        # all: LoginRequiredMixin's own redirect-to-login only happens
        # inside super().dispatch() below, so an anonymous request
        # would otherwise crash on AnonymousUser having no such
        # attribute instead of being sent to the login page.
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not request.user.totp_enabled:
            return redirect("two-factor-setup")
        return super().dispatch(request, *args, **kwargs)


class TwoFactorSetupView(LoginRequiredMixin, FormView):
    """Profile → Two-factor authentication → "Set up" — GET generates
    (or reuses, if this is a retry) a TOTP secret and shows it as a QR
    code plus the raw text fallback; POST confirms the user actually
    configured it correctly before `totp_enabled` ever flips to True.
    See apps.accounts.twofactor's own module docstring for why the
    secret is written to the user as soon as setup starts, not only
    once confirmed."""

    template_name = "accounts/two_factor_setup.html"
    form_class = TwoFactorSetupConfirmForm

    def dispatch(self, request, *args, **kwargs):
        # See TwoFactorManageView.dispatch's comment above — same
        # reason for checking is_authenticated before totp_enabled.
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.totp_enabled:
            messages.info(request, _("Two-factor authentication is already enabled."))
            return redirect("profile")
        if not request.user.totp_secret:
            request.user.totp_secret = twofactor.generate_totp_secret()
            request.user.save(update_fields=["totp_secret"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        uri = twofactor.provisioning_uri(self.request.user, self.request.user.totp_secret)
        context["qr_code_data_uri"] = twofactor.qr_code_data_uri(uri)
        context["secret"] = self.request.user.totp_secret
        return context

    def form_valid(self, form):
        self.request.user.totp_enabled = True
        self.request.user.save(update_fields=["totp_enabled"])
        # Backup-code generation is deliberately *not* done here — see
        # TwoFactorBackupCodesView's own docstring for why (it takes
        # long enough, on Django's own deliberately-slow password
        # hasher, to need its own loading state rather than making
        # this request hang silently for it).
        return redirect(f"{reverse('two-factor-backup-codes')}?welcome=1")


class TwoFactorDisableView(LoginRequiredMixin, FormView):
    """Requires the account's own password — see
    apps.accounts.forms.TwoFactorDisableForm's own docstring for why
    this isn't just a JS confirm() like most other destructive actions
    in this app."""

    template_name = "accounts/two_factor_disable.html"
    form_class = TwoFactorDisableForm

    def dispatch(self, request, *args, **kwargs):
        # See TwoFactorManageView.dispatch's comment above — same
        # reason for checking is_authenticated before totp_enabled.
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not request.user.totp_enabled:
            return redirect("profile")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        user = self.request.user
        user.totp_enabled = False
        user.totp_secret = ""
        user.save(update_fields=["totp_enabled", "totp_secret"])
        user.backup_codes.all().delete()
        messages.success(self.request, _("Two-factor authentication turned off."))
        return redirect("profile")


class AccountDeleteView(LoginRequiredMixin, FormView):
    """Profile → "Delete account" — GDPR Article 17 self-service
    erasure. See apps.accounts.services.delete_account for what's
    actually hard-deleted vs. reassigned to a shared owner, and
    apps.accounts.forms.AccountDeleteForm for the password-or-
    username confirmation step."""

    template_name = "accounts/account_delete.html"
    form_class = AccountDeleteForm
    success_url = reverse_lazy("login")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        # The one thing this app won't let a self-service action do:
        # delete the last remaining superuser and leave the instance
        # with no one able to reach Django admin at all. An ordinary
        # (non-superuser) account has no such restriction — any number
        # of those can freely delete themselves.
        if request.user.is_superuser:
            other_superusers = (
                get_user_model()
                .objects.filter(is_superuser=True)
                .exclude(pk=request.user.pk)
                .exists()
            )
            if not other_superusers:
                messages.error(
                    request,
                    _(
                        "You're the only superuser on this instance — promote another "
                        "account to superuser first, or this instance would have no one "
                        "left who can reach Django admin."
                    ),
                )
                return redirect("profile")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        user = self.request.user
        account_services.delete_account(user)
        logout(self.request)
        messages.success(
            self.request, _("Your account and all its data have been deleted.")
        )
        return super().form_valid(form)


def _rows_for_section(key, value):
    """Normalizes one apps.accounts.services.export_account_data
    section into a flat list of dicts — every field a plain, already-
    JSON-safe value — for the CSV/HTML views below, which both need
    the same tabular shape regardless of a section's own underlying
    representation: `account` is a single dict, and export_account_data
    hand-builds a growing set of other sections (api_keys,
    push_subscriptions, and every apps.social section) as flat lists
    of dicts already, rather than through Django's generic serializer
    (a list of `{"model", "pk", "fields"}` dicts). Detecting that shape
    from the data itself — instead of hardcoding which keys are
    hand-built — means a future hand-built section doesn't silently
    KeyError here the way api_keys's social-section siblings once did
    (they were added to export_account_data without this function
    being told about them)."""
    if key == "account":
        return [value]
    if not value or "pk" not in value[0] or "fields" not in value[0]:
        return value
    return [{"id": entry["pk"], **entry["fields"]} for entry in value]


class DataExportView(LoginRequiredMixin, View):
    """Profile → "Download your data" — GDPR Article 20 ("right to
    data portability"). See apps.accounts.services.export_account_data
    for exactly what's included and why. Four ways to get at the same
    export, `?format=` picking which: `json` (the complete, structured
    export, the same shape a user's own API key could already fetch),
    `csv` (a .zip of one .csv file per section, the practical format
    for opening in a spreadsheet), `html` (a single downloadable file
    to keep or hand someone else — see _data_export_standalone.html's
    own docstring for why that's a dedicated, self-contained template
    rather than a save of the page below), and no param at all for
    that same page, reusing this app's normal styling and nav, to
    read right here without downloading anything."""

    template_name = "accounts/data_export.html"

    def get(self, request):
        fmt = request.GET.get("format")
        if fmt == "json":
            return self._json_response(request.user)
        if fmt == "csv":
            return self._csv_response(request.user)
        sections = self._sections(request.user)
        if fmt == "html":
            return self._html_download_response(request, sections)
        return render(request, self.template_name, {"sections": sections})

    def _sections(self, user):
        data = account_services.export_account_data(user)
        return {key: _rows_for_section(key, value) for key, value in data.items()}

    def _html_download_response(self, request, sections):
        html = render_to_string(
            "accounts/_data_export_standalone.html",
            {"sections": sections, "username": request.user.username},
            request=request,
        )
        response = HttpResponse(html, content_type="text/html")
        response["Content-Disposition"] = (
            f'attachment; filename="ironstack-{request.user.username}-data.html"'
        )
        return response

    def _json_response(self, user):
        data = account_services.export_account_data(user)
        response = HttpResponse(
            json.dumps(data, indent=2, ensure_ascii=False), content_type="application/json"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="ironstack-{user.username}-data.json"'
        )
        return response

    def _csv_response(self, user):
        data = account_services.export_account_data(user)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for key, value in data.items():
                rows = _rows_for_section(key, value)
                if not rows:
                    continue
                text_buffer = io.StringIO()
                writer = csv.DictWriter(text_buffer, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
                archive.writestr(f"{key}.csv", text_buffer.getvalue())
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = (
            f'attachment; filename="ironstack-{user.username}-data.zip"'
        )
        return response


class TwoFactorRegenerateBackupCodesView(LoginRequiredMixin, View):
    """Replaces every existing backup code with a fresh set — the only
    recovery path if they're ever used up (or lost) without disabling
    2FA outright first. A plain confirm-then-POST (JS confirm(), like
    most other destructive-ish actions here) rather than a password
    re-entry like disabling: unlike turning 2FA off, this can't weaken
    an account's own protection, only invalidate codes that might
    already be lost anyway.

    Doesn't generate anything itself any more — see
    TwoFactorBackupCodesView's own docstring for why; this POST is now
    only the "yes, I meant to click that" confirmation step, and just
    hands off to the page that actually does the (slow) work."""

    def post(self, request, *args, **kwargs):
        if not request.user.totp_enabled:
            raise Http404
        return redirect("two-factor-backup-codes")


class TwoFactorBackupCodesView(LoginRequiredMixin, View):
    """Replaces this user's backup codes and shows the new ones — the
    landing page for both TwoFactorSetupView's own confirm step and
    TwoFactorRegenerateBackupCodesView above, neither of which do the
    actual generation themselves any more.

    Split into two requests deliberately: `generate_backup_codes`
    hashes each of `twofactor.BACKUP_CODE_COUNT` codes with Django's
    own password hasher (deliberately expensive work, the same reason
    a login attempt itself isn't instant) — on ordinary hardware that
    measures in *seconds*, not milliseconds, and used to happen
    silently inside the same request that also rendered this page,
    which looked exactly like nothing was happening at all. Reported
    live: a user hit "Regenerate" a second time during that silent
    wait, which (for the equivalent moment during initial setup) raced
    against `TwoFactorSetupView.dispatch`'s own already-enabled check
    and bounced them to their profile with no chance to ever see the
    codes their first click had already generated.

    Now: this view's own GET renders instantly, with a visible loading
    state (templates/accounts/two_factor_backup_codes.html) that
    itself triggers TwoFactorBackupCodesFragmentView below via HTMX on
    page load — the slow part happens in *that* request instead,
    swapped into this page once it completes, with no way to
    double-submit it by accident (nothing to click a second time)."""

    def get(self, request, *args, **kwargs):
        if not request.user.totp_enabled:
            raise Http404
        return render(
            request,
            "accounts/two_factor_backup_codes.html",
            {"just_enabled": request.GET.get("welcome") == "1"},
        )


class TwoFactorBackupCodesFragmentView(LoginRequiredMixin, View):
    """The actual (slow) backup-code generation — see
    TwoFactorBackupCodesView's own docstring for why this is split out
    into its own request, loaded via HTMX rather than inline."""

    def post(self, request, *args, **kwargs):
        if not request.user.totp_enabled:
            raise Http404
        codes = twofactor.generate_backup_codes(request.user)
        return render(
            request, "accounts/_two_factor_backup_codes_fragment.html", {"backup_codes": codes}
        )


class OnboardingView(LoginRequiredMixin, View):
    """Handles both submit paths of templates/accounts/
    _onboarding_modal.html (globally included from base.html, gated by
    apps.accounts.context_processors.onboarding): "Save" and "Not now"
    are two submit buttons on the same form, distinguished by the
    `action` value, since skipping still has to mark the prompt seen
    the same way saving does — otherwise it would just reappear on the
    very next page. HTMX-driven like the rest of this app's forms: a
    failed validation re-renders the same fragment with field errors
    (still targeting the modal's own container), a successful save or
    skip re-renders it with `show_onboarding` False, which is just the
    fragment's own empty wrapper `<div>` — the modal disappears from
    the page without a full navigation either way."""

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "skip":
            request.user.onboarding_completed = True
            request.user.save(update_fields=["onboarding_completed"])
            return render(request, "accounts/_onboarding_modal.html", {})

        form = OnboardingForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            return render(request, "accounts/_onboarding_modal.html", {})
        return render(
            request,
            "accounts/_onboarding_modal.html",
            {"onboarding_form": form, "show_onboarding": True},
        )
