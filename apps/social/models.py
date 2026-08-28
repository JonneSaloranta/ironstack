"""Friends, groups, and messaging — see docs/SOCIAL.md for the domain
model narrated in full (why blocking doesn't remove a shared group,
what the two User privacy flags actually gate, the invite link's
threat model). This module is deliberately just the schema; every
rule about who's allowed to do what lives in apps.social.services
instead (CLAUDE.md: "keep business/domain logic out of views" applies
just as much to models — nothing here should ever need to know about
a request or a view).
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class FriendRequestStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    ACCEPTED = "accepted", _("Accepted")
    DECLINED = "declined", _("Declined")


class FriendRequest(TimeStampedModel):
    """One user asking another to become friends. Kept around after a
    decision (not deleted) so "declined" has a record and a second
    request to the same person doesn't just silently look like the
    first one — apps.social.services enforces the actual rules (can't
    send if already friends, if the recipient has friend requests
    turned off, or if either side has blocked the other; a reverse
    pending request auto-accepts instead of creating a second row).
    """

    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sent_friend_requests",
        on_delete=models.CASCADE,
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="received_friend_requests",
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        max_length=10, choices=FriendRequestStatus.choices, default=FriendRequestStatus.PENDING
    )
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["from_user", "to_user"], name="unique_friend_request")
        ]

    def __str__(self):
        return f"{self.from_user} → {self.to_user} ({self.status})"


class Friendship(TimeStampedModel):
    """A confirmed, mutual friendship — created by
    apps.social.services.accept_friend_request, never directly.
    Always stored with user_low.pk < user_high.pk (enforced in
    services._ordered_pair, not here) so the same friendship is never
    stored twice in either direction; "my friends" is every row with
    me on either side, resolved to "the other user" in
    apps.social.services.friends_of.
    """

    user_low = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="friendships_as_low", on_delete=models.CASCADE
    )
    user_high = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="friendships_as_high", on_delete=models.CASCADE
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user_low", "user_high"], name="unique_friendship")
        ]

    def __str__(self):
        return f"{self.user_low} ↔ {self.user_high}"


class Block(TimeStampedModel):
    """`blocker` has blocked `blocked`. One-directional by design (A
    blocking B doesn't require B to also block A back) — but
    apps.social.services checks both directions everywhere a block
    matters (friend requests, direct messages, direct group invites),
    so in practice either side blocking is enough to stop 1:1
    interaction between the two. Deliberately does *not* remove either
    user from a group they're both already in — see docs/SOCIAL.md
    "Blocking" for why that's left to the group's own owner/admin.
    """

    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="blocking", on_delete=models.CASCADE
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="blocked_by", on_delete=models.CASCADE
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["blocker", "blocked"], name="unique_block")]

    def __str__(self):
        return f"{self.blocker} blocked {self.blocked}"


class MutedFriend(TimeStampedModel):
    """`user` has muted push notifications from `muted_user` — the
    exact same one-directional shape as `Block` above (me muting you
    is my own private state, never symmetric: it doesn't mute you from
    me), reused for that reason rather than adding fields to
    `Friendship`. Unlike `Block`, muting has no effect on anything but
    push (docs/SOCIAL.md "Muting") — the friendship, the ability to
    message, and the message itself all stay exactly as they were;
    only `apps.core.push.send_push_notification` ever checks this."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="muted_friends", on_delete=models.CASCADE
    )
    muted_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="muted_by", on_delete=models.CASCADE
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "muted_user"], name="unique_muted_friend")
        ]

    def __str__(self):
        return f"{self.user} muted {self.muted_user}"


class GroupRole(models.TextChoices):
    OWNER = "owner", _("Owner")
    ADMIN = "admin", _("Admin")
    MEMBER = "member", _("Member")


class Group(TimeStampedModel):
    """A user-created group with its own message thread. `owner` is
    `SET_NULL` rather than `CASCADE` — deleting the account that
    created a group shouldn't delete the group and its message history
    out from under everyone still in it, the same "history stays
    trustworthy" reasoning CLAUDE.md applies to workout data; ownership
    itself lives on GroupMembership.role, transferable to another
    member, not tied to this FK surviving.

    `invite_code`/`invite_enabled` back the /group/invite/<code>/ link
    (apps.social.crypto.generate_invite_code) — `invite_enabled`
    defaults off, since a group being joinable by anyone with a link is
    a deliberate choice an owner/admin makes (apps.social.services.
    enable_invite/disable_invite/regenerate_invite_code), not this
    group's default state.
    """

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="owned_groups",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    invite_code = models.CharField(max_length=16, unique=True, null=True, blank=True)
    invite_enabled = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class GroupMembership(TimeStampedModel):
    """One row per (group, user) — also doubles as this user's
    per-group read-state (`last_read_at`) for the unread-message badge,
    rather than a separate per-message read-state table: there's
    already exactly one row per group a user belongs to, so one more
    field is simpler than a second model.

    `last_read_at` defaults to "now" (join time) rather than being
    nullable — deliberately, so "unread" is always the single
    comparison `GroupMessage.created_at > membership.last_read_at`,
    with no separate "never read anything yet" case to special-case in
    every query that touches it. It also gives the right behavior for
    free: a message sent before someone joined a group was never
    "unread" for them, since they weren't there to read it.

    `notifications_muted` is this same "one row per (group, user)"
    shape put to a second use, the same reasoning `last_read_at`
    already follows: this user's own private push-notification
    preference for this one group (docs/SOCIAL.md "Muting"), affecting
    nothing else — the message is still created, still visible, still
    counted unread; only `apps.core.push.send_push_notification` skips
    a member with this set.
    """

    group = models.ForeignKey(Group, related_name="memberships", on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="group_memberships", on_delete=models.CASCADE
    )
    role = models.CharField(max_length=10, choices=GroupRole.choices, default=GroupRole.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)
    last_read_at = models.DateTimeField(default=timezone.now)
    notifications_muted = models.BooleanField(default=False)

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(fields=["group", "user"], name="unique_group_membership")
        ]

    def __str__(self):
        return f"{self.user} in {self.group} ({self.role})"


class GroupInviteStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    ACCEPTED = "accepted", _("Accepted")
    DECLINED = "declined", _("Declined")


class GroupInvite(TimeStampedModel):
    """A direct, in-app invite to a specific person — distinct from
    the group's own /group/invite/<code>/ link: this is what
    User.allow_group_invites actually gates (the link itself can't be
    gated per-recipient, since whoever holds it can already use it).
    """

    group = models.ForeignKey(Group, related_name="invites", on_delete=models.CASCADE)
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="group_invites_received", on_delete=models.CASCADE
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="group_invites_sent", on_delete=models.CASCADE
    )
    status = models.CharField(
        max_length=10, choices=GroupInviteStatus.choices, default=GroupInviteStatus.PENDING
    )
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["group", "invited_user"], name="unique_group_invite")
        ]

    def __str__(self):
        return f"{self.invited_by} invited {self.invited_user} to {self.group}"


class BaseMessage(TimeStampedModel):
    """Shared shape for DirectMessage/GroupMessage — text-only,
    immutable (no editing, no delete flag): the confirmed scope for
    this first version. `body`'s max length is enforced in the form,
    not here, matching Feedback.message's existing precedent.
    """

    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    body = models.TextField()

    class Meta:
        abstract = True
        ordering = ["created_at"]


class DirectMessage(BaseMessage):
    """A message between two friends. apps.social.services.
    send_direct_message refuses to create one unless the two are
    actually friends and neither has blocked the other — existing
    message history is never retroactively hidden by a later block,
    the same "history stays trustworthy" principle Group.owner's
    SET_NULL above follows."""

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="sent_direct_messages", on_delete=models.CASCADE
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="received_direct_messages",
        on_delete=models.CASCADE,
    )
    read_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.sender} → {self.recipient}: {self.body[:30]}"


class GroupMessage(BaseMessage):
    """A message in a group's shared thread. apps.social.services.
    send_group_message refuses to create one unless the sender has a
    GroupMembership row for that group."""

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="sent_group_messages", on_delete=models.CASCADE
    )
    group = models.ForeignKey(Group, related_name="messages", on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.sender} in {self.group}: {self.body[:30]}"
