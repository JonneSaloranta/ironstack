"""Every rule about who's allowed to friend/invite/message/block whom
lives here, not in views or templates (CLAUDE.md: "keep business/
domain logic out of Django views"). Each function either succeeds or
raises `SocialError` with a translated, user-facing message a view can
show directly — mirrors apps.accounts.services.delete_account's own
shape (a plain function per operation, not a class), just with an
explicit exception instead of a bare assumption the caller already
checked everything.
"""

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .crypto import generate_invite_code
from .models import (
    Block,
    DirectMessage,
    FriendRequest,
    FriendRequestStatus,
    Friendship,
    Group,
    GroupInvite,
    GroupInviteStatus,
    GroupMembership,
    GroupMessage,
    GroupRole,
)


class SocialError(Exception):
    """Raised for any rule violation above a plain 404/permission
    check (already-friends, blocked, privacy setting off, ...) — a
    view catches this and re-renders with `str(error)` as a flash
    message, rather than every caller re-deriving the same checks."""


def _ordered_pair(a, b):
    """Canonical (low, high) ordering by pk, so a Friendship/Block-style
    unordered pair is never stored twice in either direction."""
    return (a, b) if a.pk < b.pk else (b, a)


def are_friends(a, b):
    low, high = _ordered_pair(a, b)
    return Friendship.objects.filter(user_low=low, user_high=high).exists()


def is_blocked(a, b):
    """True if either has blocked the other — the two directions are
    deliberately never distinguished by any caller; see Block's own
    docstring for why."""
    return Block.objects.filter(blocker=a, blocked=b).exists() or Block.objects.filter(
        blocker=b, blocked=a
    ).exists()


def blocked_either_direction_ids(user):
    """Every user id `user` has blocked or been blocked by, one query
    — apps.social.views_friends.friend_search uses this to exclude
    them from search results entirely, not just from who can
    successfully be friend-requested."""
    blocking = Block.objects.filter(blocker=user).values_list("blocked_id", flat=True)
    blocked_by = Block.objects.filter(blocked=user).values_list("blocker_id", flat=True)
    return set(blocking) | set(blocked_by)


def friends_of(user):
    """Every other user `user` is friends with, as a list of User
    objects — resolves Friendship's low/high storage back to "the
    other person" so callers never have to think about the ordering."""
    friendships = Friendship.objects.filter(user_low=user) | Friendship.objects.filter(
        user_high=user
    )
    return [f.user_high if f.user_low_id == user.pk else f.user_low for f in friendships]


@transaction.atomic
def send_friend_request(from_user, to_user):
    if from_user == to_user:
        raise SocialError(_("You can't send yourself a friend request."))
    if is_blocked(from_user, to_user):
        raise SocialError(_("You can't send a friend request to this user."))
    if are_friends(from_user, to_user):
        raise SocialError(_("You're already friends."))
    if not to_user.allow_friend_requests:
        raise SocialError(_("This user isn't accepting friend requests."))
    existing = FriendRequest.objects.filter(from_user=from_user, to_user=to_user).first()
    if existing and existing.status == FriendRequestStatus.PENDING:
        raise SocialError(_("You already sent a friend request to this user."))
    # A reverse request already waiting ("they asked first") — accept
    # that one instead of creating a second, competing row.
    reverse = FriendRequest.objects.filter(
        from_user=to_user, to_user=from_user, status=FriendRequestStatus.PENDING
    ).first()
    if reverse:
        accept_friend_request(reverse, acting_user=from_user)
        return reverse
    if existing:
        existing.status = FriendRequestStatus.PENDING
        existing.responded_at = None
        existing.save(update_fields=["status", "responded_at"])
        return existing
    return FriendRequest.objects.create(from_user=from_user, to_user=to_user)


@transaction.atomic
def accept_friend_request(friend_request, acting_user):
    if friend_request.to_user_id != acting_user.pk:
        raise SocialError(_("You can't respond to this friend request."))
    if friend_request.status != FriendRequestStatus.PENDING:
        raise SocialError(_("This friend request has already been answered."))
    friend_request.status = FriendRequestStatus.ACCEPTED
    friend_request.responded_at = timezone.now()
    friend_request.save(update_fields=["status", "responded_at"])
    low, high = _ordered_pair(friend_request.from_user, friend_request.to_user)
    Friendship.objects.get_or_create(user_low=low, user_high=high)
    return friend_request


def decline_friend_request(friend_request, acting_user):
    if friend_request.to_user_id != acting_user.pk:
        raise SocialError(_("You can't respond to this friend request."))
    if friend_request.status != FriendRequestStatus.PENDING:
        raise SocialError(_("This friend request has already been answered."))
    friend_request.status = FriendRequestStatus.DECLINED
    friend_request.responded_at = timezone.now()
    friend_request.save(update_fields=["status", "responded_at"])
    return friend_request


def remove_friend(user, other):
    low, high = _ordered_pair(user, other)
    Friendship.objects.filter(user_low=low, user_high=high).delete()


@transaction.atomic
def block_user(blocker, blocked):
    if blocker == blocked:
        raise SocialError(_("You can't block yourself."))
    Block.objects.get_or_create(blocker=blocker, blocked=blocked)
    remove_friend(blocker, blocked)
    FriendRequest.objects.filter(
        from_user__in=[blocker, blocked],
        to_user__in=[blocker, blocked],
        status=FriendRequestStatus.PENDING,
    ).update(status=FriendRequestStatus.DECLINED, responded_at=timezone.now())


def unblock_user(blocker, blocked):
    Block.objects.filter(blocker=blocker, blocked=blocked).delete()


def send_direct_message(sender, recipient, body):
    if is_blocked(sender, recipient):
        raise SocialError(_("You can't message this user."))
    if not are_friends(sender, recipient):
        raise SocialError(_("You can only message friends."))
    return DirectMessage.objects.create(sender=sender, recipient=recipient, body=body)


def mark_direct_thread_read(user, other):
    DirectMessage.objects.filter(sender=other, recipient=user, read_at__isnull=True).update(
        read_at=timezone.now()
    )


def unread_direct_message_count(user):
    return DirectMessage.objects.filter(recipient=user, read_at__isnull=True).count()


def has_unread_direct_messages(user):
    """Same as unread_direct_message_count(user) > 0, but .exists()
    stops at the first match instead of counting every row — the
    social_badge context processor (context_processors.py) only ever
    needs the boolean, and runs on every page load for every logged-in
    user."""
    return DirectMessage.objects.filter(recipient=user, read_at__isnull=True).exists()


# --- Groups ---------------------------------------------------------


def membership_of(group, user):
    return GroupMembership.objects.filter(group=group, user=user).first()


def can_manage_group(user, group):
    membership = membership_of(group, user)
    return membership is not None and membership.role in (GroupRole.OWNER, GroupRole.ADMIN)


def is_group_owner(user, group):
    membership = membership_of(group, user)
    return membership is not None and membership.role == GroupRole.OWNER


@transaction.atomic
def create_group(owner, name, description=""):
    group = Group.objects.create(name=name, description=description, owner=owner)
    GroupMembership.objects.create(group=group, user=owner, role=GroupRole.OWNER)
    return group


def enable_invite(group):
    if not group.invite_code:
        group.invite_code = generate_invite_code()
    group.invite_enabled = True
    group.save(update_fields=["invite_code", "invite_enabled"])
    return group


def disable_invite(group):
    group.invite_enabled = False
    group.save(update_fields=["invite_enabled"])
    return group


def regenerate_invite_code(group):
    group.invite_code = generate_invite_code()
    group.save(update_fields=["invite_code"])
    return group


@transaction.atomic
def join_group_by_code(user, code):
    group = Group.objects.filter(invite_code=code, invite_enabled=True).first()
    if group is None:
        raise SocialError(_("This invite link isn't valid, or is no longer active."))
    membership, created = GroupMembership.objects.get_or_create(
        group=group, user=user, defaults={"role": GroupRole.MEMBER}
    )
    return group, created


@transaction.atomic
def invite_to_group(group, invited_by, invited_user):
    if membership_of(group, invited_by) is None:
        raise SocialError(_("You're not a member of this group."))
    if membership_of(group, invited_user) is not None:
        raise SocialError(_("This user is already in the group."))
    # "Invite a friend" is the actual feature (docs/SOCIAL.md,
    # templates/social/group_detail.html's own friend-only dropdown)
    # — enforced here too, not just in the dropdown's queryset, since
    # a crafted POST could otherwise name any user on the instance
    # regardless of what the form offered. No separate is_blocked
    # check needed: block_user always removes the friendship too, so
    # a blocked pair already fails are_friends on its own.
    if not are_friends(invited_by, invited_user):
        raise SocialError(_("You can only invite a friend."))
    if not invited_user.allow_group_invites:
        raise SocialError(_("This user isn't accepting group invites."))
    existing = GroupInvite.objects.filter(group=group, invited_user=invited_user).first()
    if existing and existing.status == GroupInviteStatus.PENDING:
        raise SocialError(_("This user has already been invited."))
    if existing:
        existing.status = GroupInviteStatus.PENDING
        existing.invited_by = invited_by
        existing.responded_at = None
        existing.save(update_fields=["status", "invited_by", "responded_at"])
        return existing
    return GroupInvite.objects.create(group=group, invited_user=invited_user, invited_by=invited_by)


@transaction.atomic
def accept_group_invite(invite, acting_user):
    if invite.invited_user_id != acting_user.pk:
        raise SocialError(_("You can't respond to this invite."))
    if invite.status != GroupInviteStatus.PENDING:
        raise SocialError(_("This invite has already been answered."))
    invite.status = GroupInviteStatus.ACCEPTED
    invite.responded_at = timezone.now()
    invite.save(update_fields=["status", "responded_at"])
    GroupMembership.objects.get_or_create(
        group=invite.group, user=acting_user, defaults={"role": GroupRole.MEMBER}
    )
    return invite


def decline_group_invite(invite, acting_user):
    if invite.invited_user_id != acting_user.pk:
        raise SocialError(_("You can't respond to this invite."))
    if invite.status != GroupInviteStatus.PENDING:
        raise SocialError(_("This invite has already been answered."))
    invite.status = GroupInviteStatus.DECLINED
    invite.responded_at = timezone.now()
    invite.save(update_fields=["status", "responded_at"])
    return invite


@transaction.atomic
def remove_group_member(group, acting_user, target_user):
    if not can_manage_group(acting_user, group):
        raise SocialError(_("You don't have permission to remove members from this group."))
    target_membership = membership_of(group, target_user)
    if target_membership is None:
        raise SocialError(_("This user isn't a member of this group."))
    if target_membership.role == GroupRole.OWNER:
        raise SocialError(_("The group owner can't be removed."))
    target_membership.delete()


def leave_group(group, user):
    membership = membership_of(group, user)
    if membership is None:
        raise SocialError(_("You're not a member of this group."))
    if membership.role == GroupRole.OWNER:
        raise SocialError(
            _("Transfer ownership to someone else before leaving a group you own.")
        )
    membership.delete()


@transaction.atomic
def set_member_role(group, acting_user, target_user, role):
    if not is_group_owner(acting_user, group):
        raise SocialError(_("Only the group owner can change member roles."))
    if target_user == acting_user:
        raise SocialError(_("You can't change your own role."))
    target_membership = membership_of(group, target_user)
    if target_membership is None:
        raise SocialError(_("This user isn't a member of this group."))
    target_membership.role = role
    target_membership.save(update_fields=["role"])
    return target_membership


@transaction.atomic
def transfer_ownership(group, acting_user, new_owner):
    """The other half of leave_group's own error message ("transfer
    ownership to someone else before leaving a group you own") — also
    what apps.accounts.services.delete_account calls automatically
    (via _successor_for) before deleting an account that owns a group
    with other members still in it, so deleting your account can never
    leave a group permanently unmanageable (no one left with OWNER/
    ADMIN role at all)."""
    if not is_group_owner(acting_user, group):
        raise SocialError(_("Only the group owner can transfer ownership."))
    if new_owner == acting_user:
        raise SocialError(_("You're already the owner."))
    new_membership = membership_of(group, new_owner)
    if new_membership is None:
        raise SocialError(_("This user isn't a member of this group."))
    old_membership = membership_of(group, acting_user)
    old_membership.role = GroupRole.ADMIN
    old_membership.save(update_fields=["role"])
    new_membership.role = GroupRole.OWNER
    new_membership.save(update_fields=["role"])
    group.owner = new_owner
    group.save(update_fields=["owner"])
    return group


def _successor_for(group, departing_user):
    """The best candidate to inherit ownership of `group` once
    `departing_user` (its current owner) is gone: the longest-standing
    admin if there is one, otherwise the longest-standing member —
    `joined_at` ordering (GroupMembership.Meta.ordering) already gives
    "oldest first" for free. None if departing_user was the only
    member, in which case there's no one left to transfer to."""
    candidates = GroupMembership.objects.filter(group=group).exclude(user=departing_user)
    admin = candidates.filter(role=GroupRole.ADMIN).first()
    return admin.user if admin else (candidates.first().user if candidates.exists() else None)


@transaction.atomic
def delete_group(group, acting_user):
    if not is_group_owner(acting_user, group):
        raise SocialError(_("Only the group owner can delete the group."))
    group.delete()


def reassign_owned_groups_before_deletion(user):
    """Called by apps.accounts.services.delete_account, before
    user.delete() cascades this user's own GroupMembership rows away.
    Without this, a group whose only OWNER-role member deletes their
    account would cascade straight to "no member has OWNER or ADMIN
    role at all" — permanently unmanageable (no one could ever change
    its invite link, roles, or delete it) even though it might still
    have other members and message history. Mirrors delete_account's
    own "reassign shared content, don't just delete it" reasoning for
    a custom Exercise/Food/etc."""
    owned = GroupMembership.objects.filter(user=user, role=GroupRole.OWNER).select_related(
        "group"
    )
    for membership in owned:
        successor = _successor_for(membership.group, user)
        if successor is not None:
            transfer_ownership(membership.group, acting_user=user, new_owner=successor)


def send_group_message(group, sender, body):
    if membership_of(group, sender) is None:
        raise SocialError(_("You're not a member of this group."))
    return GroupMessage.objects.create(group=group, sender=sender, body=body)


def mark_group_read(group, user):
    GroupMembership.objects.filter(group=group, user=user).update(last_read_at=timezone.now())


def unread_group_message_count(user, group=None):
    """Total unread group-message count across every group `user`
    belongs to, or for one specific `group` if given. One query
    regardless of how many groups that is: `GroupMembership.
    last_read_at` always has a value (see its own field comment for
    why it's never null), so "unread" is a single `created_at > ...`
    comparison joined straight through to each message's own
    membership row — not a separate query per group. An earlier
    version looped over memberships in Python, one .count() query per
    group; harmless for one group, but this function is also called
    with no `group` (every group at once) from
    apps.social.context_processors.social_badge on *every* page
    load for every logged-in user, where that loop meant one extra
    query per group that user is in, unconditionally, site-wide."""
    filters = {
        "group__memberships__user": user,
        "created_at__gt": models.F("group__memberships__last_read_at"),
    }
    if group is not None:
        filters["group"] = group
    return GroupMessage.objects.filter(**filters).count()


def has_unread_group_messages(user):
    """Same query as unread_group_message_count(user), but .exists()
    instead of .count() — the badge only ever needs a boolean, and
    can stop at the first match instead of counting every row."""
    return GroupMessage.objects.filter(
        group__memberships__user=user,
        created_at__gt=models.F("group__memberships__last_read_at"),
    ).exists()


def unread_group_message_counts_by_group(user):
    """{group_id: unread_count} for every group `user` belongs to, in
    one query — what apps.social.views_groups.group_list and
    apps.social.views_messages.message_list actually need (an unread
    count *per row* of a list), instead of calling
    unread_group_message_count once per group in a Python loop (still
    one query each with the fix above, but still N queries for N
    groups shown on one page)."""
    counts = (
        GroupMessage.objects.filter(
            group__memberships__user=user,
            created_at__gt=models.F("group__memberships__last_read_at"),
        )
        .values("group")
        .annotate(unread=models.Count("id"))
    )
    return {row["group"]: row["unread"] for row in counts}


def pending_friend_request_count(user):
    return FriendRequest.objects.filter(to_user=user, status=FriendRequestStatus.PENDING).count()


def has_pending_friend_requests(user):
    """See has_unread_direct_messages's own comment — same reasoning."""
    return FriendRequest.objects.filter(
        to_user=user, status=FriendRequestStatus.PENDING
    ).exists()


def pending_group_invite_count(user):
    return GroupInvite.objects.filter(
        invited_user=user, status=GroupInviteStatus.PENDING
    ).count()


def has_pending_group_invites(user):
    """See has_unread_direct_messages's own comment — same reasoning."""
    return GroupInvite.objects.filter(
        invited_user=user, status=GroupInviteStatus.PENDING
    ).exists()
