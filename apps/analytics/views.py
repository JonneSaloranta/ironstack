from datetime import date

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView, TemplateView

from apps.core import units as core_units
from apps.exercises.services import visible_to as exercises_visible_to

from . import dateranges, services


def _resolve_range_from_request(request):
    start_raw = request.GET.get("start")
    end_raw = request.GET.get("end")
    start = date.fromisoformat(start_raw) if start_raw else None
    end = date.fromisoformat(end_raw) if end_raw else None
    range_key = request.GET.get("range", dateranges.DEFAULT_RANGE)
    return dateranges.resolve(range_key, start=start, end=end)


class AnalyticsDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "analytics/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        date_range = _resolve_range_from_request(self.request)

        context["date_range"] = date_range
        context["range_choices"] = dateranges.RANGE_CHOICES
        context["summary"] = services.training_summary(user, date_range)
        context["weekly_volume_chart"] = services.weekly_volume_series(user, date_range)
        context["muscle_group_chart"] = services.muscle_group_volume_series(user, date_range)
        context["recent_prs"] = services.pr_history(user, date_range, limit=15)
        context["weight_unit_label"] = core_units.weight_unit_label(user.unit_system)
        return context


class ExerciseAnalyticsView(LoginRequiredMixin, DetailView):
    template_name = "analytics/exercise_analytics.html"
    context_object_name = "exercise"

    def get_queryset(self):
        return exercises_visible_to(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        exercise = self.object
        date_range = _resolve_range_from_request(self.request)

        context["date_range"] = date_range
        context["range_choices"] = dateranges.RANGE_CHOICES
        context["summary"] = services.exercise_summary(user, exercise, date_range)
        context["one_rm_chart"] = services.exercise_one_rm_trend(user, exercise, date_range)
        context["weight_unit_label"] = core_units.weight_unit_label(user.unit_system)
        return context
