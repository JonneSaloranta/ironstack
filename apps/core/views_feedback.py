"""Profile → Feedback (any signed-in user) and Profile → Administration
→ Feedback (staff only) — see apps.core.models.Feedback/FeedbackSettings
for what's actually stored and why this is a one-way inbox rather than
a two-way conversation thread."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, ListView

from apps.core.forms import FeedbackForm, FeedbackSettingsForm
from apps.core.mixins import StaffRequiredMixin
from apps.core.models import Feedback, FeedbackSettings


class FeedbackCreateView(LoginRequiredMixin, CreateView):
    form_class = FeedbackForm
    template_name = "core/feedback_form.html"
    success_url = reverse_lazy("profile")

    def dispatch(self, request, *args, **kwargs):
        # Gate the URL itself, not just the profile page's link to it —
        # same reasoning as apps.accounts.views.SignupView's own
        # SIGNUP_ENABLED check: a hidden link doesn't stop someone who
        # already knows/guesses the path.
        if not FeedbackSettings.load().enabled:
            messages.info(request, _("Feedback isn't currently being accepted."))
            return redirect("profile")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.user = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, _("Thanks for the feedback!"))
        return response


class FeedbackListView(StaffRequiredMixin, ListView):
    model = Feedback
    template_name = "core/feedback_list.html"
    context_object_name = "feedback_items"
    paginate_by = 30

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault(
            "settings_form", FeedbackSettingsForm(instance=FeedbackSettings.load())
        )
        return context

    def post(self, request, *args, **kwargs):
        form = FeedbackSettingsForm(request.POST, instance=FeedbackSettings.load())
        if form.is_valid():
            form.save()
            messages.success(request, _("Feedback settings saved."))
            return redirect("feedback-list")
        # Re-render with the invalid form's own errors rather than
        # redirecting, the same as apps.core.views_backup.BackupListView.
        self.object_list = self.get_queryset()
        context = self.get_context_data(settings_form=form)
        return self.render_to_response(context)
