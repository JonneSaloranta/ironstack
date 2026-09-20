from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.generic import DetailView, TemplateView, View

from apps.core import units as core_units
from apps.exercises.services import visible_to as exercises_visible_to

from . import achievements, dateranges, services


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
        context["recent_prs"] = services.pr_history_grouped_by_exercise(user, date_range, limit=15)
        context["weight_unit_label"] = core_units.weight_unit_label(user.unit_system)
        context["trained_exercises"] = services.trained_exercises(user)
        return context


class MemberProfileView(LoginRequiredMixin, View):
    """Another user's public fitness profile — asked for directly:
    recent PRs, achievement highlights, and how long they've been a
    member. Deliberately nothing nutrition/body-weight related (no
    logged foods, no weight readings, no measurements) — only the same
    workout/PR/streak scope `achievements.highlights_for` already has.

    Gated by `User.show_achievements`, the same "share my training
    activity" opt-out the achievements carousel and "Recently active"
    list already use (see that field's own docstring) — not a second,
    separate privacy setting for what would otherwise be the exact
    same decision. A user can always view their *own* profile
    regardless of this setting (it hides your profile from others, not
    from yourself) — matched with `request.user == member` rather than
    folding it into the queryset filter, so a 404 for a genuinely
    opted-out member stays a plain, unconditional 404 for anyone else.
    """

    template_name = "analytics/member_profile.html"

    def get(self, request, username):
        User = get_user_model()
        member = get_object_or_404(User, username=username)
        if not member.show_achievements and member != request.user:
            raise Http404
        recent_prs = services.pr_history_grouped_by_exercise(
            member, dateranges.resolve("all"), limit=10
        )
        # A PT's own profile is the surface a prospective client
        # requests coaching from — apps.coaching has no separate
        # "browse trainers" page, since a coaching relationship is
        # only ever proposed by a coachee who already knows who
        # they're asking (same discovery model apps.social's own
        # friend_search already gives friend requests).
        active_coaching = None
        pending_coaching_request = False
        if member.is_personal_trainer and member != request.user:
            from apps.coaching import services as coaching_services
            from apps.coaching.models import CoachingRequest, CoachingRequestStatus

            active_coaching = coaching_services.get_active_relationship(member, request.user)
            if active_coaching is None:
                pending_coaching_request = CoachingRequest.objects.filter(
                    coach=member, coachee=request.user, status=CoachingRequestStatus.PENDING
                ).exists()
        return render(
            request,
            self.template_name,
            {
                "member": member,
                "display_name": member.public_display_name(),
                "highlights": achievements.highlights_for(member),
                "recent_prs": recent_prs,
                "active_coaching": active_coaching,
                "pending_coaching_request": pending_coaching_request,
            },
        )


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
