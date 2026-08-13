from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.views.generic import TemplateView

from apps.workouts.models import WorkoutSessionStatus
from apps.workouts.services import sessions_for


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["in_progress_session"] = (
            sessions_for(self.request.user)
            .filter(status=WorkoutSessionStatus.IN_PROGRESS)
            .first()
        )
        return context


def healthcheck(request):
    """Unauthenticated liveness endpoint for Docker/reverse-proxy checks."""
    return HttpResponse("ok")
