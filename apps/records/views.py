from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from apps.exercises.services import visible_to as exercises_visible_to

from . import services


class ExerciseRecordsView(LoginRequiredMixin, TemplateView):
    """A user's current PRs for one exercise — computed live from history,
    see apps.records.services.current_records."""

    template_name = "records/exercise_records.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        exercise = get_object_or_404(
            exercises_visible_to(self.request.user), pk=kwargs["exercise_pk"]
        )
        context["exercise"] = exercise
        context["records"] = services.current_records(self.request.user, exercise)
        return context
