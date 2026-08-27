from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import models
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.core.request import client_ip

from . import services
from .forms import GroupForm, GroupInviteForm
from .models import Group, GroupInvite, GroupInviteStatus, GroupMembership, GroupRole

User = get_user_model()

# Same shape as apps.accounts.forms's login/password-reset throttles:
# not because the invite code itself is guessable (see
# apps.social.crypto's own comment on its entropy), but because this
# codebase treats a cheap second layer as the norm wherever a secret
# is looked up by an anonymous-ish request.
INVITE_LOOKUP_LIMIT = 20
INVITE_LOOKUP_WINDOW_SECONDS = 15 * 60


@login_required
def group_list(request):
    memberships = list(
        GroupMembership.objects.filter(user=request.user).select_related("group")
    )
    unread_by_group = services.unread_group_message_counts_by_group(request.user)
    # One query for every group's total member count, not one query
    # per row — same reasoning as unread_group_message_counts_by_group
    # just above. A plain group_id__in=[...] filter, not a self-join
    # through the reverse `group__memberships` relation: Django's
    # "spanning multi-valued relationships" behavior would multiply
    # rows there and inflate the count.
    member_counts = dict(
        GroupMembership.objects.filter(group_id__in=[m.group_id for m in memberships])
        .values_list("group")
        .annotate(count=models.Count("id"))
    )
    groups = [
        {
            "group": m.group,
            "role": m.role,
            "unread": unread_by_group.get(m.group_id, 0),
            "member_count": member_counts.get(m.group_id, 1),
        }
        for m in memberships
    ]
    incoming_invites = GroupInvite.objects.filter(
        invited_user=request.user, status=GroupInviteStatus.PENDING
    ).select_related("group", "invited_by")
    return render(
        request,
        "social/group_list.html",
        {"groups": groups, "incoming_invites": incoming_invites},
    )


@login_required
def group_create(request):
    if request.method == "POST":
        form = GroupForm(request.POST)
        if form.is_valid():
            group = services.create_group(
                request.user, form.cleaned_data["name"], form.cleaned_data["description"]
            )
            messages.success(request, _("Group created."))
            return redirect("social:group-detail", pk=group.pk)
    else:
        form = GroupForm()
    return render(request, "social/group_form.html", {"form": form, "group": None})


def _member_or_404(group, user):
    membership = services.membership_of(group, user)
    if membership is None:
        raise Http404
    return membership


@login_required
def group_detail(request, pk):
    group = get_object_or_404(Group, pk=pk)
    membership = _member_or_404(group, request.user)
    members = GroupMembership.objects.filter(group=group).select_related("user")
    friend_ids = {f.pk for f in services.friends_of(request.user)}
    member_ids = {m.user_id for m in members}
    invitable_friends = [
        f for f in services.friends_of(request.user) if f.pk not in member_ids
    ]
    invitable_ids = [f.pk for f in invitable_friends]
    invite_form = GroupInviteForm(choices_queryset=User.objects.filter(pk__in=invitable_ids))
    return render(
        request,
        "social/group_detail.html",
        {
            "group": group,
            "membership": membership,
            "members": members,
            "can_manage": services.can_manage_group(request.user, group),
            "is_owner": services.is_group_owner(request.user, group),
            "invite_form": invite_form,
            "friend_ids": friend_ids,
            "GroupRole": GroupRole,
        },
    )


@login_required
def group_edit(request, pk):
    group = get_object_or_404(Group, pk=pk)
    _member_or_404(group, request.user)
    if not services.can_manage_group(request.user, group):
        messages.error(request, _("You don't have permission to edit this group."))
        return redirect("social:group-detail", pk=pk)
    if request.method == "POST":
        form = GroupForm(request.POST, instance=group)
        if form.is_valid():
            form.save()
            messages.success(request, _("Group updated."))
            return redirect("social:group-detail", pk=pk)
    else:
        form = GroupForm(instance=group)
    return render(request, "social/group_form.html", {"form": form, "group": group})


@login_required
def group_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    group = get_object_or_404(Group, pk=pk)
    try:
        services.delete_group(group, request.user)
        messages.success(request, _("Group deleted."))
    except services.SocialError as error:
        messages.error(request, str(error))
        return redirect("social:group-detail", pk=pk)
    return redirect("social:group-list")


@login_required
def group_invite_toggle(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    group = get_object_or_404(Group, pk=pk)
    if not services.can_manage_group(request.user, group):
        messages.error(request, _("You don't have permission to manage this group's invite link."))
        return redirect("social:group-detail", pk=pk)
    if group.invite_enabled:
        services.disable_invite(group)
        messages.success(request, _("Invite link disabled."))
    else:
        services.enable_invite(group)
        messages.success(request, _("Invite link enabled."))
    return redirect("social:group-detail", pk=pk)


@login_required
def group_invite_regenerate(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    group = get_object_or_404(Group, pk=pk)
    if not services.can_manage_group(request.user, group):
        messages.error(request, _("You don't have permission to manage this group's invite link."))
        return redirect("social:group-detail", pk=pk)
    services.regenerate_invite_code(group)
    messages.success(request, _("Invite link regenerated — the old link no longer works."))
    return redirect("social:group-detail", pk=pk)


@login_required
def group_invite_send(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    group = get_object_or_404(Group, pk=pk)
    _member_or_404(group, request.user)
    friend_id = request.POST.get("friend")
    invited_user = get_object_or_404(User, pk=friend_id)
    try:
        services.invite_to_group(group, invited_by=request.user, invited_user=invited_user)
        messages.success(request, _("Invite sent."))
    except services.SocialError as error:
        messages.error(request, str(error))
    return redirect("social:group-detail", pk=pk)


@login_required
def group_invite_respond(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    invite = get_object_or_404(GroupInvite, pk=pk, invited_user=request.user)
    action = request.POST.get("action")
    try:
        if action == "accept":
            services.accept_group_invite(invite, acting_user=request.user)
            messages.success(request, _("Joined the group."))
            return redirect("social:group-detail", pk=invite.group_id)
        elif action == "decline":
            services.decline_group_invite(invite, acting_user=request.user)
            messages.success(request, _("Invite declined."))
        else:
            messages.error(request, _("Unknown action."))
    except services.SocialError as error:
        messages.error(request, str(error))
    return redirect("social:group-list")


@login_required
def group_member_remove(request, pk, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    group = get_object_or_404(Group, pk=pk)
    target = get_object_or_404(User, pk=user_id)
    try:
        services.remove_group_member(group, acting_user=request.user, target_user=target)
        messages.success(request, _("Member removed."))
    except services.SocialError as error:
        messages.error(request, str(error))
    return redirect("social:group-detail", pk=pk)


@login_required
def group_member_role(request, pk, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    group = get_object_or_404(Group, pk=pk)
    target = get_object_or_404(User, pk=user_id)
    role = request.POST.get("role")
    if role not in (GroupRole.ADMIN, GroupRole.MEMBER):
        messages.error(request, _("Unknown role."))
        return redirect("social:group-detail", pk=pk)
    try:
        services.set_member_role(group, acting_user=request.user, target_user=target, role=role)
        messages.success(request, _("Role updated."))
    except services.SocialError as error:
        messages.error(request, str(error))
    return redirect("social:group-detail", pk=pk)


@login_required
def group_transfer_ownership(request, pk, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    group = get_object_or_404(Group, pk=pk)
    new_owner = get_object_or_404(User, pk=user_id)
    try:
        services.transfer_ownership(group, acting_user=request.user, new_owner=new_owner)
        messages.success(request, _("Ownership transferred."))
    except services.SocialError as error:
        messages.error(request, str(error))
    return redirect("social:group-detail", pk=pk)


@login_required
def group_leave(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    group = get_object_or_404(Group, pk=pk)
    try:
        services.leave_group(group, request.user)
        messages.success(request, _("Left the group."))
    except services.SocialError as error:
        messages.error(request, str(error))
        return redirect("social:group-detail", pk=pk)
    return redirect("social:group-list")


def group_invite_join(request, code):
    """The /group/invite/<code>/ landing page — deliberately outside
    the social: namespace (config/urls.py registers it directly), and
    deliberately GET-then-confirm rather than joining the instant the
    link is opened, so a link preview fetch (Slack/Discord/iMessage
    unfurling it) can't silently add the account that shared it.

    Deliberately NOT @login_required, unlike every other view in this
    module: someone clicking a shared invite link is very often not
    logged in yet at all — that's the whole point of a link anyone can
    open — so this has to render something useful (the group's name,
    a "log in to join" prompt) for an anonymous request rather than
    bouncing it to the login page before ever showing what the link
    was even for. The join itself still requires being authenticated
    (checked below, not by a decorator)."""
    cache_key = f"group-invite-lookup:{client_ip(request)}"
    attempts = cache.get(cache_key, 0)
    if attempts >= INVITE_LOOKUP_LIMIT:
        messages.error(request, _("Too many attempts. Try again in a few minutes."))
        return render(request, "social/group_invite_join.html", {"group": None})

    group = Group.objects.filter(invite_code=code, invite_enabled=True).first()
    if group is None:
        cache.set(cache_key, attempts + 1, INVITE_LOOKUP_WINDOW_SECONDS)
        return render(request, "social/group_invite_join.html", {"group": None})

    already_member = (
        request.user.is_authenticated
        and services.membership_of(group, request.user) is not None
    )
    if request.method == "POST" and request.user.is_authenticated:
        try:
            services.join_group_by_code(request.user, code)
            messages.success(request, _("Joined the group."))
            return redirect("social:group-detail", pk=group.pk)
        except services.SocialError as error:
            messages.error(request, str(error))
    return render(
        request,
        "social/group_invite_join.html",
        {"group": group, "already_member": already_member},
    )
