from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.generic import View

from apps.analytics import achievements, dateranges
from apps.analytics import services as analytics_services
from apps.core import units as core_units
from apps.measurements import services as measurement_services
from apps.programs.models import Program
from apps.workouts.models import WorkoutSession

from . import services

User = get_user_model()


class ClientListView(LoginRequiredMixin, View):
    """A PT's own "My clients" — active CoachingRelationships plus the
    incoming CoachingRequests still waiting on them, same
    incoming-requests-alongside-the-list shape as apps.social.
    views_friends.friend_list."""

    template_name = "coaching/client_list.html"

    def get(self, request):
        from .models import CoachingRequest, CoachingRequestStatus

        relationships = services.clients_of(request.user)
        incoming = CoachingRequest.objects.filter(
            coach=request.user, status=CoachingRequestStatus.PENDING
        ).select_related("coachee")
        return render(
            request,
            self.template_name,
            {"coaching_relationships": relationships, "incoming_requests": incoming},
        )


class ClientDetailView(LoginRequiredMixin, View):
    """A coach's view of one specific, currently-active client's
    training and body-weight data. Gated on an accepted
    CoachingRelationship (services.get_active_relationship), NOT on
    `User.show_achievements` the way apps.analytics.views.
    MemberProfileView is — accepting the coaching request is itself
    the consent to share this (this feature's own product decision),
    so that separate, unrelated privacy toggle plays no part here. An
    ended/declined/never-requested relationship all 404 identically —
    never leaking which case it was, the same reasoning
    MemberProfileView's own opted-out 404 already follows."""

    template_name = "coaching/client_detail.html"

    def get(self, request, username):
        member = get_object_or_404(User, username=username)
        relationship = services.get_active_relationship(coach=request.user, coachee=member)
        if relationship is None:
            raise Http404
        date_range = dateranges.resolve(dateranges.DEFAULT_RANGE)
        # training_summary_canonical, not training_summary — the
        # latter converts total_volume using *member*'s own
        # unit_system, but this page is meant to display in the
        # *coach's* (request.user's) units, the same "viewer's own
        # units" precedent apps.analytics.views.MemberProfileView's
        # reused PR partial already follows.
        training_summary = analytics_services.training_summary_canonical(member, date_range)
        weight_unit_label = core_units.weight_unit_label(request.user.unit_system)
        total_volume_display = core_units.kg_to_display(
            training_summary.total_volume, request.user.unit_system
        )
        recent_prs = analytics_services.pr_history_grouped_by_exercise(
            member, dateranges.resolve("all"), limit=10
        )
        # Displayed in the *coach's* own unit preference, not the
        # client's — same "the viewer's own units, not the profile
        # owner's" precedent apps.analytics.views.MemberProfileView's
        # reused PR partial already follows, and the same manual
        # canonical-to-display conversion apps.measurements.views does
        # for a user's own history page (there's no model-level
        # property that does this automatically).
        from apps.measurements import units as measurement_units

        measurement_types = measurement_services.visible_to(member)
        measurements = []
        for measurement_type in measurement_types:
            latest = measurement_services.latest_for(member, measurement_type)
            stats = measurement_services.stats_for(member, measurement_type)
            unit_label = measurement_units.display_unit_label(
                measurement_type.unit_kind, request.user.unit_system
            )
            measurements.append(
                {
                    "type": measurement_type,
                    "unit_label": unit_label,
                    "latest_display": (
                        measurement_units.to_display(
                            latest.value, measurement_type.unit_kind, request.user.unit_system
                        )
                        if latest
                        else None
                    ),
                    "stats": stats,
                }
            )
        assigned_programs = []
        for program in Program.objects.filter(owner=request.user, shared_with_clients=member):
            client_copy = program.imported_copies.filter(owner=member).first()
            if client_copy is None:
                continue
            last_session = (
                WorkoutSession.objects.filter(user=member, program=client_copy)
                .order_by("-started_at")
                .first()
            )
            assigned_programs.append(
                {"program": program, "client_copy": client_copy, "last_session": last_session}
            )
        return render(
            request,
            self.template_name,
            {
                "member": member,
                "display_name": member.public_display_name(),
                "relationship": relationship,
                "highlights": achievements.highlights_for(member),
                "training_summary": training_summary,
                "weight_unit_label": weight_unit_label,
                "total_volume_display": total_volume_display,
                "recent_prs": recent_prs,
                "measurements": measurements,
                "assigned_programs": assigned_programs,
            },
        )
