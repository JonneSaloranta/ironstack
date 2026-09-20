from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from . import services
from .models import CoachingRelationship, CoachingRequest, CoachingRequestStatus

User = get_user_model()


@login_required
def my_coaches(request):
    """A coachee's own view: active coaches + their own outgoing
    pending requests — mirrors apps.social.views_friends.friend_list's
    incoming/outgoing split, just one-directional (a coachee has no
    "incoming" side of this particular relationship)."""
    coaching = services.coaches_of(request.user)
    outgoing = CoachingRequest.objects.filter(
        coachee=request.user, status=CoachingRequestStatus.PENDING
    ).select_related("coach")
    return render(
        request,
        "coaching/my_coaches.html",
        {"coaching_relationships": coaching, "outgoing_requests": outgoing},
    )


@login_required
def coaching_request_send(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    coach = get_object_or_404(User, pk=user_id)
    try:
        services.send_coaching_request(request.user, coach)
        messages.success(request, _("Coaching request sent."))
    except services.CoachingError as error:
        messages.error(request, str(error))
    return redirect(request.META.get("HTTP_REFERER") or reverse("coaching:my-coaches"))


@login_required
def coaching_request_respond(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    coaching_request = get_object_or_404(CoachingRequest, pk=pk, coach=request.user)
    action = request.POST.get("action")
    try:
        if action == "accept":
            services.accept_coaching_request(coaching_request, acting_user=request.user)
            messages.success(request, _("Coaching request accepted."))
        elif action == "decline":
            services.decline_coaching_request(coaching_request, acting_user=request.user)
            messages.success(request, _("Coaching request declined."))
        else:
            messages.error(request, _("Unknown action."))
    except services.CoachingError as error:
        messages.error(request, str(error))
    return redirect("coaching:client-list")


@login_required
def coaching_relationship_end(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    # Not pre-filtered to `coach=request.user` or `coachee=request.user`
    # in the queryset itself — either side may end it, and
    # services.end_coaching_relationship already does the real "are
    # you actually part of this relationship" check, raising
    # CoachingError (shown as a flash message) rather than a bare 404
    # for a relationship that exists but isn't this user's own.
    relationship = get_object_or_404(CoachingRelationship, pk=pk)
    try:
        services.end_coaching_relationship(relationship, acting_user=request.user)
        messages.success(request, _("Coaching relationship ended."))
    except services.CoachingError as error:
        messages.error(request, str(error))
    is_coach = relationship.coach_id == request.user.pk
    return redirect("coaching:client-list" if is_coach else "coaching:my-coaches")
