from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import models
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from . import services
from .forms import MessageForm
from .models import DirectMessage, Group, GroupMembership, GroupMessage

User = get_user_model()

# The polling fragments below re-render the whole thread every 4
# seconds (templates/social/message_thread.html/group_thread.html) —
# with no cap, a long-running conversation would mean re-querying and
# re-rendering its *entire* history on every single poll tick,
# forever. No pagination UI exists (or was asked for) to go further
# back than this; that's a deliberate "smallest coherent
# implementation" choice, not an oversight.
THREAD_MESSAGE_LIMIT = 100


def _last_n_ascending(queryset, n):
    """The last `n` rows of an ascending-ordered queryset, still in
    ascending order — order_by("-created_at")[:n] gives the most
    recent n newest-first, which this reverses back to chronological
    (oldest of the visible window first) for display."""
    return list(reversed(list(queryset.order_by("-created_at")[:n])))


@login_required
def message_list(request):
    """The social hub — conversation list (friends with a thread,
    groups you're in) plus anything waiting on a response (friend
    requests, group invites), so it doubles as the one entry point
    linked from the profile page rather than needing its own separate
    landing page."""
    friends = services.friends_of(request.user)
    # One query for every friend's unread count, not one query per
    # friend in a Python loop — see apps.social.services.
    # unread_group_message_counts_by_group's own comment for the same
    # concern applied to groups just below.
    unread_dm_by_sender = dict(
        DirectMessage.objects.filter(recipient=request.user, read_at__isnull=True)
        .values_list("sender")
        .annotate(unread=models.Count("id"))
    )
    friend_threads = [
        {"friend": f, "unread": unread_dm_by_sender.get(f.pk, 0)} for f in friends
    ]
    memberships = GroupMembership.objects.filter(user=request.user).select_related("group")
    unread_by_group = services.unread_group_message_counts_by_group(request.user)
    group_threads = [
        {"group": m.group, "unread": unread_by_group.get(m.group_id, 0)} for m in memberships
    ]
    return render(
        request,
        "social/message_list.html",
        {
            "friend_threads": friend_threads,
            "group_threads": group_threads,
            "pending_friend_requests": services.pending_friend_request_count(request.user),
            "pending_group_invites": services.pending_group_invite_count(request.user),
        },
    )


@login_required
def message_thread(request, user_id):
    other = get_object_or_404(User, pk=user_id)
    if not services.are_friends(request.user, other):
        raise Http404
    return render(request, "social/message_thread.html", {"other": other, "form": MessageForm()})


@login_required
def message_thread_fragment(request, user_id):
    """Polled by message_thread.html (`hx-trigger="load, every 4s"`)
    and also what a message-send POST targets — one render path for
    both "a new message just arrived" and "I just sent one", so
    there's no second, subtly different code path for the sender's
    own message appearing (see apps/social's own plan notes)."""
    other = get_object_or_404(User, pk=user_id)
    if not services.are_friends(request.user, other):
        raise Http404
    if request.method == "POST":
        form = MessageForm(request.POST)
        if form.is_valid():
            try:
                services.send_direct_message(request.user, other, form.cleaned_data["body"])
            except services.SocialError as error:
                messages.error(request, str(error))
    services.mark_direct_thread_read(request.user, other)
    thread_messages = _last_n_ascending(
        DirectMessage.objects.filter(
            sender__in=[request.user, other], recipient__in=[request.user, other]
        ).select_related("sender"),
        THREAD_MESSAGE_LIMIT,
    )
    return render(
        request,
        "social/_message_thread_fragment.html",
        {"other": other, "thread_messages": thread_messages},
    )


@login_required
def group_thread(request, pk):
    group = get_object_or_404(Group, pk=pk)
    if services.membership_of(group, request.user) is None:
        raise Http404
    return render(request, "social/group_thread.html", {"group": group, "form": MessageForm()})


@login_required
def group_thread_fragment(request, pk):
    group = get_object_or_404(Group, pk=pk)
    if services.membership_of(group, request.user) is None:
        raise Http404
    if request.method == "POST":
        form = MessageForm(request.POST)
        if form.is_valid():
            try:
                services.send_group_message(group, request.user, form.cleaned_data["body"])
            except services.SocialError as error:
                messages.error(request, str(error))
    services.mark_group_read(group, request.user)
    thread_messages = _last_n_ascending(
        GroupMessage.objects.filter(group=group).select_related("sender"), THREAD_MESSAGE_LIMIT
    )
    return render(
        request,
        "social/_group_thread_fragment.html",
        {"group": group, "thread_messages": thread_messages},
    )
