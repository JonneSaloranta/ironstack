from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from . import services
from .models import Block, FriendRequest, FriendRequestStatus

User = get_user_model()


@login_required
def friend_list(request):
    friends = services.friends_of(request.user)
    incoming = FriendRequest.objects.filter(
        to_user=request.user, status=FriendRequestStatus.PENDING
    ).select_related("from_user")
    outgoing = FriendRequest.objects.filter(
        from_user=request.user, status=FriendRequestStatus.PENDING
    ).select_related("to_user")
    return render(
        request,
        "social/friend_list.html",
        {"friends": friends, "incoming_requests": incoming, "outgoing_requests": outgoing},
    )


@login_required
def friend_search(request):
    query = request.GET.get("q", "").strip()
    results = []
    if query:
        friend_ids = [f.pk for f in services.friends_of(request.user)]
        # Excluded from the queryset itself, before the [:20] slice —
        # not filtered out of an already-sliced Python list, which
        # could silently return fewer than 20 (or zero) results even
        # when 20+ real matches exist further down, if the ones that
        # happened to sort first were all friends. Blocked users are
        # excluded too (either direction): sending them a request
        # would just fail anyway (services.send_friend_request), so
        # there's no reason for them to show up as a result at all —
        # the point of a block is not needing to see that person.
        results = list(
            User.objects.filter(username__icontains=query)
            .exclude(pk=request.user.pk)
            .exclude(pk__in=friend_ids)
            .exclude(pk__in=services.blocked_either_direction_ids(request.user))[:20]
        )
    template = (
        "social/_friend_search_results.html"
        if request.headers.get("HX-Request")
        else "social/friend_search.html"
    )
    return render(request, template, {"query": query, "results": results})


@login_required
def friend_request_send(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    to_user = get_object_or_404(User, pk=user_id)
    try:
        services.send_friend_request(request.user, to_user)
        messages.success(request, _("Friend request sent."))
    except services.SocialError as error:
        messages.error(request, str(error))
    return redirect(request.META.get("HTTP_REFERER") or reverse("social:friend-list"))


@login_required
def friend_request_respond(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    friend_request = get_object_or_404(FriendRequest, pk=pk, to_user=request.user)
    action = request.POST.get("action")
    try:
        if action == "accept":
            services.accept_friend_request(friend_request, acting_user=request.user)
            messages.success(request, _("Friend request accepted."))
        elif action == "decline":
            services.decline_friend_request(friend_request, acting_user=request.user)
            messages.success(request, _("Friend request declined."))
        else:
            messages.error(request, _("Unknown action."))
    except services.SocialError as error:
        messages.error(request, str(error))
    return redirect("social:friend-list")


@login_required
def friend_remove(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    other = get_object_or_404(User, pk=user_id)
    services.remove_friend(request.user, other)
    messages.success(request, _("Removed from your friends."))
    return redirect("social:friend-list")


@login_required
def block_list(request):
    blocks = Block.objects.filter(blocker=request.user).select_related("blocked")
    return render(request, "social/block_list.html", {"blocks": blocks})


@login_required
def block_user_view(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User, pk=user_id)
    try:
        services.block_user(request.user, target)
        messages.success(request, _("User blocked."))
    except services.SocialError as error:
        messages.error(request, str(error))
    return redirect(request.META.get("HTTP_REFERER") or reverse("social:block-list"))


@login_required
def unblock_user_view(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User, pk=user_id)
    services.unblock_user(request.user, target)
    messages.success(request, _("User unblocked."))
    return redirect("social:block-list")
