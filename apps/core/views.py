from datetime import timedelta

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.views.generic import TemplateView

from apps.analytics import dateranges
from apps.analytics import services as analytics_services
from apps.measurements import services as measurement_services
from apps.measurements import units as measurement_units
from apps.measurements.models import MeasurementType
from apps.workouts.models import WorkoutSessionStatus
from apps.workouts.services import sessions_for


class DashboardView(LoginRequiredMixin, TemplateView):
    """docs/UI.md "Dashboard — Possible content": next/last workout (the
    in-progress banner below), this week's volume, recent PRs, body
    weight, recent activity (that last one is left to its own section —
    apps.activities already has a dedicated, working history per type;
    duplicating it here would just be a second, staler copy)."""

    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["in_progress_session"] = (
            sessions_for(user).filter(status=WorkoutSessionStatus.IN_PROGRESS).first()
        )

        today = timezone.localdate()
        this_week = dateranges.resolve(None, start=today - timedelta(days=today.weekday()))
        context["week_summary"] = analytics_services.training_summary(user, this_week)
        context["recent_prs"] = analytics_services.pr_history(
            user, dateranges.resolve("30d"), limit=3
        )

        body_weight_type = MeasurementType.objects.filter(name="Body weight", owner=None).first()
        if body_weight_type:
            latest = measurement_services.latest_for(user, body_weight_type)
            context["body_weight"] = (
                measurement_units.to_display(
                    latest.value, body_weight_type.unit_kind, user.unit_system
                )
                if latest
                else None
            )
            context["body_weight_unit"] = measurement_units.display_unit_label(
                body_weight_type.unit_kind, user.unit_system
            )
        return context


def healthcheck(request):
    """Unauthenticated liveness endpoint for Docker/reverse-proxy checks."""
    return HttpResponse("ok")


def _serve_static_root_file(filename, content_type):
    """Serve a file from the `static/` source directory at the *site
    root* rather than under `/static/`. A service worker's default scope
    is the directory it's served from — `/sw.js` covers the whole app,
    `/static/sw.js` would only ever cover `/static/`, which is useless.
    Reads the source file directly (not STATIC_ROOT), so this works
    whether or not `collectstatic` has run yet.
    """
    path = settings.BASE_DIR / "static" / filename
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        raise Http404 from None
    response = HttpResponse(content, content_type=content_type)
    if filename == "sw.js":
        response["Service-Worker-Allowed"] = "/"
    return response


def service_worker(request):
    return _serve_static_root_file("sw.js", "application/javascript")


def web_manifest(request):
    return _serve_static_root_file("manifest.json", "application/manifest+json")
