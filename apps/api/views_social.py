"""Friends, groups and messages (apps.social) through the API - issue #37.

Every rule stays in apps.social.services, exactly as for the web: these
views only find the rows the key's user may see (404 otherwise) and
turn a SocialError into a 400 with its message in the user's language.
Other users appear by id and username only, as on the web's social
pages. Social context."""

from functools import wraps

from django.contrib.auth import get_user_model
from django.db import models
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.social import services as social_services
from apps.social.forms import MESSAGE_MAX_LENGTH
from apps.social.models import (
    Block,
    DirectMessage,
    FriendRequest,
    FriendRequestStatus,
    Group,
    GroupInvite,
    GroupInviteStatus,
    GroupMembership,
    GroupMessage,
    GroupRole,
    MutedFriend,
)

from .models import ApiContext
from .views import _user_language

User = get_user_model()

#: Same window the web thread shows (apps.social.views_messages).
THREAD_MESSAGE_LIMIT = 100


# --------------------------------------------------------------------
# Serializers
# --------------------------------------------------------------------


class SocialUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()


class FriendSerializer(SocialUserSerializer):
    muted = serializers.BooleanField(help_text="Push notifications from them muted by you.")


class FriendRequestSerializer(serializers.ModelSerializer):
    from_user = SocialUserSerializer()
    to_user = SocialUserSerializer()

    class Meta:
        model = FriendRequest
        fields = ["id", "from_user", "to_user", "status", "created_at", "responded_at"]


class UserIdSerializer(serializers.Serializer):
    user = serializers.IntegerField()


class MessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    sender = SocialUserSerializer()
    body = serializers.CharField()
    created_at = serializers.DateTimeField()


class MessageBodySerializer(serializers.Serializer):
    body = serializers.CharField(max_length=MESSAGE_MAX_LENGTH)


class GroupSerializer(serializers.ModelSerializer):
    """`role`, `unread` and `notifications_muted` are the user's own;
    `invite_code` is only shown to an owner or admin, as on the web."""

    role = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    unread = serializers.SerializerMethodField()
    notifications_muted = serializers.SerializerMethodField()
    invite_code = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = [
            "id", "name", "description", "role", "member_count", "unread",
            "notifications_muted", "invite_enabled", "invite_code", "created_at",
        ]
        read_only_fields = ["invite_enabled", "created_at"]

    def _membership(self, group):
        return self.context["memberships"].get(group.pk)

    def get_role(self, group) -> str:
        return self._membership(group).role

    def get_member_count(self, group) -> int:
        return self.context["member_counts"].get(group.pk, 1)

    def get_unread(self, group) -> int:
        return self.context["unread"].get(group.pk, 0)

    def get_notifications_muted(self, group) -> bool:
        return self._membership(group).notifications_muted

    def get_invite_code(self, group) -> str | None:
        if self._membership(group).role in (GroupRole.OWNER, GroupRole.ADMIN):
            return group.invite_code
        return None


class GroupMemberSerializer(serializers.ModelSerializer):
    user = SocialUserSerializer()

    class Meta:
        model = GroupMembership
        fields = ["user", "role", "joined_at"]


class GroupInviteSerializer(serializers.ModelSerializer):
    group = serializers.SerializerMethodField()
    invited_by = SocialUserSerializer()

    class Meta:
        model = GroupInvite
        fields = ["id", "group", "invited_by", "status", "created_at"]

    def get_group(self, invite) -> dict:
        return {"id": invite.group_id, "name": invite.group.name}


class ConversationsSerializer(serializers.Serializer):
    friends = serializers.ListField(child=serializers.DictField())
    groups = serializers.ListField(child=serializers.DictField())
    pending_friend_requests = serializers.IntegerField()
    pending_group_invites = serializers.IntegerField()


# --------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------


def social_errors(method):
    """Runs a view method in the user's language and answers a
    SocialError (already friends, blocked, not allowed, ...) with a 400
    carrying its message - the web's flash message."""

    @wraps(method)
    def wrapper(self, request, *args, **kwargs):
        with _user_language(request.user):
            try:
                return method(self, request, *args, **kwargs)
            except social_services.SocialError as error:
                return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

    return wrapper


class SocialView(APIView):
    api_context = ApiContext.SOCIAL


def _user_from(data):
    serializer = UserIdSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    return get_object_or_404(User, pk=serializer.validated_data["user"])


def _friend_or_404(user, user_id):
    other = get_object_or_404(User, pk=user_id)
    if not social_services.are_friends(user, other):
        from django.http import Http404

        raise Http404
    return other


def _member_group_or_404(user, pk):
    group = get_object_or_404(Group, pk=pk)
    membership = social_services.membership_of(group, user)
    if membership is None:
        from django.http import Http404

        raise Http404
    return group, membership


def _group_context(user, groups):
    ids = [group.pk for group in groups]
    return {
        "memberships": {
            m.group_id: m for m in GroupMembership.objects.filter(user=user, group_id__in=ids)
        },
        "member_counts": dict(
            GroupMembership.objects.filter(group_id__in=ids)
            .values_list("group")
            .annotate(count=models.Count("id"))
        ),
        "unread": social_services.unread_group_message_counts_by_group(user),
    }


def _group_data(user, group):
    group = Group.objects.get(pk=group.pk)
    return GroupSerializer(group, context=_group_context(user, [group])).data


def _thread(queryset, after):
    """The last THREAD_MESSAGE_LIMIT messages, oldest first, or only the
    ones newer than message id `after` (for polling)."""
    queryset = queryset.select_related("sender")
    if after is not None:
        return list(queryset.filter(pk__gt=after).order_by("created_at", "pk"))
    return list(reversed(list(queryset.order_by("-created_at", "-pk")[:THREAD_MESSAGE_LIMIT])))


def _after(request):
    raw = request.query_params.get("after")
    if raw is None:
        return None
    if not raw.isdigit():
        raise serializers.ValidationError({"after": "Must be a message id."})
    return int(raw)


_AFTER_PARAMETER = OpenApiParameter(
    "after", int, description="Only messages newer than this message id (for polling)."
)


# --------------------------------------------------------------------
# Friends
# --------------------------------------------------------------------


class FriendListView(SocialView):
    """`GET social/friends/` - the user's friends."""

    @extend_schema(responses=FriendSerializer(many=True))
    def get(self, request):
        muted = set(
            MutedFriend.objects.filter(user=request.user).values_list("muted_user_id", flat=True)
        )
        friends = sorted(social_services.friends_of(request.user), key=lambda u: u.username)
        return Response(
            FriendSerializer(
                [{"id": f.pk, "username": f.username, "muted": f.pk in muted} for f in friends],
                many=True,
            ).data
        )


class FriendDetailView(SocialView):
    """`DELETE social/friends/<user_id>/` - unfriend."""

    @extend_schema(responses={204: None})
    def delete(self, request, user_id):
        other = _friend_or_404(request.user, user_id)
        social_services.remove_friend(request.user, other)
        return Response(status=status.HTTP_204_NO_CONTENT)


class FriendMuteView(SocialView):
    """`POST social/friends/<user_id>/mute/` and `.../unmute/` - push
    notifications from a friend; nothing else changes."""

    mute = True

    @extend_schema(request=None, responses={204: None})
    def post(self, request, user_id):
        other = _friend_or_404(request.user, user_id)
        if self.mute:
            social_services.mute_friend(request.user, other)
        else:
            social_services.unmute_friend(request.user, other)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserSearchView(SocialView):
    """`GET social/users/?search=` - people to send a friend request to,
    like the web's friend search: by username, at most 20, never
    yourself, a friend, or anyone blocked either way."""

    @extend_schema(
        responses=SocialUserSerializer(many=True),
        parameters=[OpenApiParameter("search", required=True)],
    )
    def get(self, request):
        query = (request.query_params.get("search") or "").strip()
        if not query:
            return Response([])
        friend_ids = [f.pk for f in social_services.friends_of(request.user)]
        users = (
            User.objects.filter(username__icontains=query)
            .exclude(pk=request.user.pk)
            .exclude(pk__in=friend_ids)
            .exclude(pk__in=social_services.blocked_either_direction_ids(request.user))
            .order_by("username")[:20]
        )
        return Response(SocialUserSerializer(users, many=True).data)


class FriendRequestListView(SocialView):
    """`GET social/friend-requests/` - pending requests to and from the
    user. `POST {user}` sends one (or accepts theirs, when they asked
    first)."""

    @extend_schema(responses=FriendRequestSerializer(many=True))
    def get(self, request):
        pending = FriendRequest.objects.filter(status=FriendRequestStatus.PENDING).filter(
            models.Q(to_user=request.user) | models.Q(from_user=request.user)
        )
        return Response(
            FriendRequestSerializer(
                pending.select_related("from_user", "to_user"), many=True
            ).data
        )

    @extend_schema(request=UserIdSerializer, responses={201: FriendRequestSerializer})
    @social_errors
    def post(self, request):
        friend_request = social_services.send_friend_request(request.user, _user_from(request.data))
        friend_request.refresh_from_db()
        return Response(
            FriendRequestSerializer(friend_request).data, status=status.HTTP_201_CREATED
        )


class FriendRequestRespondView(SocialView):
    """`POST social/friend-requests/<id>/accept/` or `.../decline/` - a
    request to the user."""

    accept = True

    @extend_schema(request=None, responses=FriendRequestSerializer)
    @social_errors
    def post(self, request, pk):
        friend_request = get_object_or_404(FriendRequest, pk=pk, to_user=request.user)
        if self.accept:
            social_services.accept_friend_request(friend_request, acting_user=request.user)
        else:
            social_services.decline_friend_request(friend_request, acting_user=request.user)
        return Response(FriendRequestSerializer(friend_request).data)


class BlockListView(SocialView):
    """`GET social/blocks/` - who the user has blocked. `POST {user}`
    blocks someone: ends the friendship and declines pending requests
    between you, as on the web."""

    @extend_schema(responses=SocialUserSerializer(many=True))
    def get(self, request):
        blocked = User.objects.filter(
            pk__in=Block.objects.filter(blocker=request.user).values("blocked")
        ).order_by("username")
        return Response(SocialUserSerializer(blocked, many=True).data)

    @extend_schema(request=UserIdSerializer, responses={204: None})
    @social_errors
    def post(self, request):
        social_services.block_user(request.user, _user_from(request.data))
        return Response(status=status.HTTP_204_NO_CONTENT)


class BlockDetailView(SocialView):
    """`DELETE social/blocks/<user_id>/` - unblock."""

    @extend_schema(responses={204: None})
    def delete(self, request, user_id):
        get_object_or_404(Block, blocker=request.user, blocked_id=user_id)
        social_services.unblock_user(request.user, get_object_or_404(User, pk=user_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------
# Conversations and direct messages
# --------------------------------------------------------------------


class ConversationListView(SocialView):
    """`GET social/conversations/` - the web's messages hub: a thread per
    friend and per group with its unread count, and how many friend
    requests and group invites wait for an answer."""

    @extend_schema(responses=ConversationsSerializer)
    def get(self, request):
        unread_by_sender = dict(
            DirectMessage.objects.filter(recipient=request.user, read_at__isnull=True)
            .values_list("sender")
            .annotate(unread=models.Count("id"))
        )
        friends = sorted(social_services.friends_of(request.user), key=lambda u: u.username)
        unread_by_group = social_services.unread_group_message_counts_by_group(request.user)
        memberships = GroupMembership.objects.filter(user=request.user).select_related("group")
        return Response(
            {
                "friends": [
                    {"id": f.pk, "username": f.username, "unread": unread_by_sender.get(f.pk, 0)}
                    for f in friends
                ],
                "groups": [
                    {
                        "id": m.group_id,
                        "name": m.group.name,
                        "unread": unread_by_group.get(m.group_id, 0),
                    }
                    for m in memberships
                ],
                "pending_friend_requests": social_services.pending_friend_request_count(
                    request.user
                ),
                "pending_group_invites": social_services.pending_group_invite_count(
                    request.user
                ),
            }
        )


class DirectMessageView(SocialView):
    """`GET social/messages/<user_id>/` - the thread with a friend, the
    last 100 messages oldest first (`?after=<id>` for only newer ones),
    marking theirs read. `POST {body}` sends one."""

    @extend_schema(responses=MessageSerializer(many=True), parameters=[_AFTER_PARAMETER])
    def get(self, request, user_id):
        other = _friend_or_404(request.user, user_id)
        after = _after(request)
        social_services.mark_direct_thread_read(request.user, other)
        messages = _thread(
            DirectMessage.objects.filter(
                sender__in=[request.user, other], recipient__in=[request.user, other]
            ),
            after,
        )
        return Response(MessageSerializer(messages, many=True).data)

    @extend_schema(request=MessageBodySerializer, responses={201: MessageSerializer})
    @social_errors
    def post(self, request, user_id):
        other = _friend_or_404(request.user, user_id)
        serializer = MessageBodySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = social_services.send_direct_message(
            request.user, other, serializer.validated_data["body"]
        )
        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------
# Groups
# --------------------------------------------------------------------


class GroupWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ["name", "description"]


class GroupListView(SocialView):
    """`GET social/groups/` - the groups the user is in. `POST {name,
    description}` creates one, owned by the user."""

    @extend_schema(responses=GroupSerializer(many=True))
    def get(self, request):
        groups = list(Group.objects.filter(memberships__user=request.user).order_by("name"))
        return Response(
            GroupSerializer(groups, many=True, context=_group_context(request.user, groups)).data
        )

    @extend_schema(request=GroupWriteSerializer, responses={201: GroupSerializer})
    def post(self, request):
        serializer = GroupWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group = social_services.create_group(
            request.user,
            serializer.validated_data["name"],
            serializer.validated_data.get("description", ""),
        )
        return Response(_group_data(request.user, group), status=status.HTTP_201_CREATED)


class GroupDetailView(SocialView):
    """`social/groups/<id>/` - members only (404 otherwise). `PATCH
    {name, description}` for an owner or admin; `DELETE` for the owner."""

    @extend_schema(responses=GroupSerializer)
    def get(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        return Response(_group_data(request.user, group))

    @extend_schema(request=GroupWriteSerializer, responses=GroupSerializer)
    def patch(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        if not social_services.can_manage_group(request.user, group):
            return Response(
                {"detail": "Only the group's owner or an admin can edit it."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = GroupWriteSerializer(group, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(_group_data(request.user, group))

    @extend_schema(responses={204: None})
    @social_errors
    def delete(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        social_services.delete_group(group, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class GroupMemberListView(SocialView):
    """`GET social/groups/<id>/members/`."""

    @extend_schema(responses=GroupMemberSerializer(many=True))
    def get(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        members = GroupMembership.objects.filter(group=group).select_related("user")
        return Response(GroupMemberSerializer(members, many=True).data)


class GroupRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=[GroupRole.ADMIN, GroupRole.MEMBER])


class GroupMemberView(SocialView):
    """`DELETE social/groups/<id>/members/<user_id>/` removes a member
    (owner or admin); `PATCH {role}` makes them an admin or a member
    (owner only)."""

    @extend_schema(responses={204: None})
    @social_errors
    def delete(self, request, pk, user_id):
        group, _membership = _member_group_or_404(request.user, pk)
        target = get_object_or_404(User, pk=user_id)
        social_services.remove_group_member(group, acting_user=request.user, target_user=target)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=GroupRoleSerializer, responses=GroupMemberSerializer)
    @social_errors
    def patch(self, request, pk, user_id):
        group, _membership = _member_group_or_404(request.user, pk)
        target = get_object_or_404(User, pk=user_id)
        serializer = GroupRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = social_services.set_member_role(
            group, acting_user=request.user, target_user=target,
            role=serializer.validated_data["role"],
        )
        return Response(GroupMemberSerializer(membership).data)


class GroupTransferView(SocialView):
    """`POST social/groups/<id>/transfer/ {user}` - the owner hands the
    group to another member and becomes an admin."""

    @extend_schema(request=UserIdSerializer, responses=GroupSerializer)
    @social_errors
    def post(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        social_services.transfer_ownership(
            group, acting_user=request.user, new_owner=_user_from(request.data)
        )
        return Response(_group_data(request.user, group))


class GroupLeaveView(SocialView):
    """`POST social/groups/<id>/leave/` - an owner has to transfer the
    group first."""

    @extend_schema(request=None, responses={204: None})
    @social_errors
    def post(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        social_services.leave_group(group, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class GroupMuteSerializer(serializers.Serializer):
    muted = serializers.BooleanField()


class GroupMuteView(SocialView):
    """`POST social/groups/<id>/mute/ {muted}` - the user's own push
    notifications for the group."""

    @extend_schema(request=GroupMuteSerializer, responses=GroupSerializer)
    def post(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        serializer = GroupMuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        social_services.set_group_muted(group, request.user, serializer.validated_data["muted"])
        return Response(_group_data(request.user, group))


class GroupInviteLinkSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    regenerate = serializers.BooleanField(required=False, default=False)


class GroupInviteLinkView(SocialView):
    """`POST social/groups/<id>/invite-link/ {enabled?, regenerate?}` -
    an owner or admin turns the group's invite code on or off, or
    replaces it so the old one stops working."""

    @extend_schema(request=GroupInviteLinkSerializer, responses=GroupSerializer)
    def post(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        if not social_services.can_manage_group(request.user, group):
            return Response(
                {"detail": "Only the group's owner or an admin can manage its invite link."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = GroupInviteLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data.get("enabled") is True:
            social_services.enable_invite(group)
        elif data.get("enabled") is False:
            social_services.disable_invite(group)
        if data["regenerate"]:
            social_services.regenerate_invite_code(group)
        return Response(_group_data(request.user, group))


class GroupJoinSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=16)


class GroupJoinView(SocialView):
    """`POST social/groups/join/ {code}` - joins with a group's invite
    code (the code at the end of its invite link)."""

    @extend_schema(request=GroupJoinSerializer, responses=GroupSerializer)
    @social_errors
    def post(self, request):
        serializer = GroupJoinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group, _created = social_services.join_group_by_code(
            request.user, serializer.validated_data["code"]
        )
        return Response(_group_data(request.user, group))


class GroupInviteSendView(SocialView):
    """`POST social/groups/<id>/invites/ {user}` - any member invites one
    of their friends who allows group invites."""

    @extend_schema(request=UserIdSerializer, responses={201: GroupInviteSerializer})
    @social_errors
    def post(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        invite = social_services.invite_to_group(
            group, invited_by=request.user, invited_user=_user_from(request.data)
        )
        return Response(GroupInviteSerializer(invite).data, status=status.HTTP_201_CREATED)


class GroupInviteListView(SocialView):
    """`GET social/group-invites/` - pending invites to the user."""

    @extend_schema(responses=GroupInviteSerializer(many=True))
    def get(self, request):
        invites = GroupInvite.objects.filter(
            invited_user=request.user, status=GroupInviteStatus.PENDING
        ).select_related("group", "invited_by")
        return Response(GroupInviteSerializer(invites, many=True).data)


class GroupInviteRespondView(SocialView):
    """`POST social/group-invites/<id>/accept/` or `.../decline/`."""

    accept = True

    @extend_schema(request=None, responses=GroupInviteSerializer)
    @social_errors
    def post(self, request, pk):
        invite = get_object_or_404(GroupInvite, pk=pk, invited_user=request.user)
        if self.accept:
            social_services.accept_group_invite(invite, acting_user=request.user)
        else:
            social_services.decline_group_invite(invite, acting_user=request.user)
        return Response(GroupInviteSerializer(invite).data)


class GroupMessageView(SocialView):
    """`GET social/groups/<id>/messages/` - the group's thread, the last
    100 messages oldest first (`?after=<id>` for only newer ones),
    marking it read. `POST {body}` sends one."""

    @extend_schema(responses=MessageSerializer(many=True), parameters=[_AFTER_PARAMETER])
    def get(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        after = _after(request)
        social_services.mark_group_read(group, request.user)
        messages = _thread(GroupMessage.objects.filter(group=group), after)
        return Response(MessageSerializer(messages, many=True).data)

    @extend_schema(request=MessageBodySerializer, responses={201: MessageSerializer})
    @social_errors
    def post(self, request, pk):
        group, _membership = _member_group_or_404(request.user, pk)
        serializer = MessageBodySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = social_services.send_group_message(
            group, request.user, serializer.validated_data["body"]
        )
        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)
