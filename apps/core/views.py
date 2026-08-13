from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.views.generic import TemplateView


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"


def healthcheck(request):
    """Unauthenticated liveness endpoint for Docker/reverse-proxy checks."""
    return HttpResponse("ok")
