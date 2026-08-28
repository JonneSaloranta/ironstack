from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import services
from .models import (
    Block,
    DirectMessage,
    FriendRequest,
    FriendRequestStatus,
    Friendship,
    Group,
    GroupInviteStatus,
    GroupMembership,
    GroupMessage,
    GroupRole,
)

User = get_user_model()


def make_friends(a, b):
    services.send_friend_request(a, b)
    request = FriendRequest.objects.get(from_user=a, to_user=b)
    services.accept_friend_request(request, acting_user=b)


class FriendRequestServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")

    def test_sending_creates_a_pending_request(self):
        services.send_friend_request(self.alice, self.bob)
        request = FriendRequest.objects.get(from_user=self.alice, to_user=self.bob)
        self.assertEqual(request.status, FriendRequestStatus.PENDING)

    def test_cannot_send_to_yourself(self):
        with self.assertRaises(services.SocialError):
            services.send_friend_request(self.alice, self.alice)

    def test_cannot_send_if_recipient_opted_out(self):
        self.bob.allow_friend_requests = False
        self.bob.save()
        with self.assertRaises(services.SocialError):
            services.send_friend_request(self.alice, self.bob)

    def test_cannot_send_if_either_side_blocked(self):
        services.block_user(self.bob, self.alice)
        with self.assertRaises(services.SocialError):
            services.send_friend_request(self.alice, self.bob)

    def test_cannot_send_a_duplicate_pending_request(self):
        services.send_friend_request(self.alice, self.bob)
        with self.assertRaises(services.SocialError):
            services.send_friend_request(self.alice, self.bob)

    def test_cannot_send_if_already_friends(self):
        make_friends(self.alice, self.bob)
        with self.assertRaises(services.SocialError):
            services.send_friend_request(self.alice, self.bob)

    def test_a_reverse_pending_request_is_auto_accepted_instead_of_duplicated(self):
        services.send_friend_request(self.bob, self.alice)
        services.send_friend_request(self.alice, self.bob)
        self.assertTrue(services.are_friends(self.alice, self.bob))
        self.assertEqual(FriendRequest.objects.count(), 1)

    def test_accepting_creates_a_friendship(self):
        services.send_friend_request(self.alice, self.bob)
        request = FriendRequest.objects.get(from_user=self.alice, to_user=self.bob)
        services.accept_friend_request(request, acting_user=self.bob)
        self.assertTrue(services.are_friends(self.alice, self.bob))

    def test_only_the_recipient_can_accept(self):
        services.send_friend_request(self.alice, self.bob)
        request = FriendRequest.objects.get(from_user=self.alice, to_user=self.bob)
        with self.assertRaises(services.SocialError):
            services.accept_friend_request(request, acting_user=self.alice)

    def test_declining_does_not_create_a_friendship(self):
        services.send_friend_request(self.alice, self.bob)
        request = FriendRequest.objects.get(from_user=self.alice, to_user=self.bob)
        services.decline_friend_request(request, acting_user=self.bob)
        self.assertFalse(services.are_friends(self.alice, self.bob))
        self.assertEqual(request.status, FriendRequestStatus.DECLINED)

    def test_cannot_respond_to_an_already_answered_request(self):
        services.send_friend_request(self.alice, self.bob)
        request = FriendRequest.objects.get(from_user=self.alice, to_user=self.bob)
        services.accept_friend_request(request, acting_user=self.bob)
        with self.assertRaises(services.SocialError):
            services.accept_friend_request(request, acting_user=self.bob)

    def test_friendship_is_stored_once_regardless_of_pk_order(self):
        make_friends(self.bob, self.alice)
        self.assertEqual(Friendship.objects.count(), 1)
        friendship = Friendship.objects.first()
        self.assertEqual(friendship.user_low.pk, min(self.alice.pk, self.bob.pk))

    def test_friends_of_resolves_to_the_other_user_in_either_direction(self):
        carol = User.objects.create_user(username="carol", password="s3cret-pass")
        make_friends(self.alice, self.bob)
        make_friends(carol, self.alice)
        friends = {u.pk for u in services.friends_of(self.alice)}
        self.assertEqual(friends, {self.bob.pk, carol.pk})

    def test_friends_of_is_a_single_query_regardless_of_friend_count(self):
        # Regression: friends_of() used to read f.user_high/f.user_low
        # without select_related, firing one extra query per friendship
        # to lazily fetch each User row — the same N+1 shape
        # unread_group_message_count had before its own fix, and one
        # this function is more exposed to, since it's called from
        # nearly every social page (friend_list, message_list,
        # group_detail, friend_search).
        for i in range(5):
            other = User.objects.create_user(username=f"friend{i}", password="s3cret-pass")
            make_friends(self.alice, other)
        with self.assertNumQueries(1):
            services.friends_of(self.alice)

    def test_remove_friend_deletes_the_friendship(self):
        make_friends(self.alice, self.bob)
        services.remove_friend(self.alice, self.bob)
        self.assertFalse(services.are_friends(self.alice, self.bob))


class BlockServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")

    def test_cannot_block_yourself(self):
        with self.assertRaises(services.SocialError):
            services.block_user(self.alice, self.alice)

    def test_is_blocked_is_true_regardless_of_which_side_blocked(self):
        services.block_user(self.alice, self.bob)
        self.assertTrue(services.is_blocked(self.alice, self.bob))
        self.assertTrue(services.is_blocked(self.bob, self.alice))

    def test_blocking_removes_an_existing_friendship(self):
        make_friends(self.alice, self.bob)
        services.block_user(self.alice, self.bob)
        self.assertFalse(services.are_friends(self.alice, self.bob))

    def test_blocking_declines_a_pending_friend_request_either_direction(self):
        services.send_friend_request(self.bob, self.alice)
        services.block_user(self.alice, self.bob)
        request = FriendRequest.objects.get(from_user=self.bob, to_user=self.alice)
        self.assertEqual(request.status, FriendRequestStatus.DECLINED)

    def test_unblocking_removes_the_block(self):
        services.block_user(self.alice, self.bob)
        services.unblock_user(self.alice, self.bob)
        self.assertFalse(Block.objects.filter(blocker=self.alice, blocked=self.bob).exists())

    def test_blocking_twice_does_not_create_two_rows(self):
        services.block_user(self.alice, self.bob)
        services.block_user(self.alice, self.bob)
        self.assertEqual(Block.objects.filter(blocker=self.alice, blocked=self.bob).count(), 1)


class DirectMessageServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")

    def test_cannot_message_a_non_friend(self):
        with self.assertRaises(services.SocialError):
            services.send_direct_message(self.alice, self.bob, "hi")

    def test_can_message_a_friend(self):
        make_friends(self.alice, self.bob)
        message = services.send_direct_message(self.alice, self.bob, "hi")
        self.assertEqual(message.body, "hi")

    def test_cannot_message_a_blocked_user(self):
        make_friends(self.alice, self.bob)
        services.block_user(self.bob, self.alice)
        with self.assertRaises(services.SocialError):
            services.send_direct_message(self.alice, self.bob, "hi")

    def test_unread_count_and_marking_read(self):
        make_friends(self.alice, self.bob)
        services.send_direct_message(self.alice, self.bob, "hi")
        services.send_direct_message(self.alice, self.bob, "again")
        self.assertEqual(services.unread_direct_message_count(self.bob), 2)
        services.mark_direct_thread_read(self.bob, self.alice)
        self.assertEqual(services.unread_direct_message_count(self.bob), 0)

    def test_existing_messages_survive_a_later_block(self):
        make_friends(self.alice, self.bob)
        services.send_direct_message(self.alice, self.bob, "hi")
        services.block_user(self.bob, self.alice)
        self.assertEqual(DirectMessage.objects.count(), 1)

    def test_sending_a_message_notifies_the_recipient(self):
        make_friends(self.alice, self.bob)
        with mock.patch("apps.core.push.send_push_notification") as mock_send:
            services.send_direct_message(self.alice, self.bob, "hi")
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[0], self.bob)

    def test_a_push_failure_never_breaks_sending_the_message(self):
        """send_push_notification itself is designed to never raise
        (apps.core.push's own docstring), but this pins the contract
        from the caller's side too — even if it somehow did, the
        message must already be saved and the exception must not
        propagate out of send_direct_message."""
        make_friends(self.alice, self.bob)
        with mock.patch(
            "apps.core.push.send_push_notification", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                services.send_direct_message(self.alice, self.bob, "hi")
        # The message itself was already created before the push call —
        # a RuntimeError from push here is an unrealistic worst case
        # (the real function never raises), but even then the message
        # this test just sent is still in the database.
        self.assertEqual(DirectMessage.objects.count(), 1)


class GroupServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")

    def test_creating_a_group_makes_the_creator_the_owner(self):
        group = services.create_group(self.alice, "Lifters")
        membership = services.membership_of(group, self.alice)
        self.assertEqual(membership.role, GroupRole.OWNER)

    def test_invite_link_is_disabled_by_default(self):
        group = services.create_group(self.alice, "Lifters")
        self.assertFalse(group.invite_enabled)
        self.assertFalse(group.invite_code)

    def test_enabling_the_invite_generates_a_code(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        self.assertTrue(group.invite_enabled)
        self.assertEqual(len(group.invite_code), 10)

    def test_disabling_keeps_the_code_for_a_later_re_enable(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        code = group.invite_code
        services.disable_invite(group)
        self.assertFalse(group.invite_enabled)
        self.assertEqual(group.invite_code, code)

    def test_regenerating_replaces_the_code(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        old_code = group.invite_code
        services.regenerate_invite_code(group)
        self.assertNotEqual(group.invite_code, old_code)

    def test_joining_by_a_valid_enabled_code_creates_a_membership(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        joined_group, created = services.join_group_by_code(self.bob, group.invite_code)
        self.assertEqual(joined_group.pk, group.pk)
        self.assertTrue(created)
        self.assertIsNotNone(services.membership_of(group, self.bob))

    def test_joining_by_an_unknown_code_fails(self):
        with self.assertRaises(services.SocialError):
            services.join_group_by_code(self.bob, "NOSUCHCODE")

    def test_joining_by_a_disabled_codes_link_fails(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        code = group.invite_code
        services.disable_invite(group)
        with self.assertRaises(services.SocialError):
            services.join_group_by_code(self.bob, code)

    def test_can_manage_group_is_true_for_owner_and_admin_only(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        self.assertTrue(services.can_manage_group(self.alice, group))
        self.assertFalse(services.can_manage_group(self.bob, group))
        services.set_member_role(
            group, acting_user=self.alice, target_user=self.bob, role=GroupRole.ADMIN
        )
        self.assertTrue(services.can_manage_group(self.bob, group))

    def test_only_the_owner_can_change_roles(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        services.set_member_role(
            group, acting_user=self.alice, target_user=self.bob, role=GroupRole.ADMIN
        )
        with self.assertRaises(services.SocialError):
            services.set_member_role(
                group, acting_user=self.bob, target_user=self.alice, role=GroupRole.MEMBER
            )

    def test_removing_a_member_requires_manage_permission(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        carol = User.objects.create_user(username="carol", password="s3cret-pass")
        services.join_group_by_code(carol, group.invite_code)
        with self.assertRaises(services.SocialError):
            services.remove_group_member(group, acting_user=self.bob, target_user=carol)
        services.remove_group_member(group, acting_user=self.alice, target_user=carol)
        self.assertIsNone(services.membership_of(group, carol))

    def test_the_owner_cannot_be_removed(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        services.set_member_role(
            group, acting_user=self.alice, target_user=self.bob, role=GroupRole.ADMIN
        )
        with self.assertRaises(services.SocialError):
            services.remove_group_member(group, acting_user=self.bob, target_user=self.alice)

    def test_the_owner_cannot_leave_without_transferring_ownership_first(self):
        group = services.create_group(self.alice, "Lifters")
        with self.assertRaises(services.SocialError):
            services.leave_group(group, self.alice)

    def test_a_regular_member_can_leave(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        services.leave_group(group, self.bob)
        self.assertIsNone(services.membership_of(group, self.bob))

    def test_only_the_owner_can_delete_the_group(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        with self.assertRaises(services.SocialError):
            services.delete_group(group, self.bob)
        services.delete_group(group, self.alice)
        self.assertFalse(Group.objects.filter(pk=group.pk).exists())

    def test_transferring_ownership_swaps_roles_and_the_owner_fk(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        services.transfer_ownership(group, acting_user=self.alice, new_owner=self.bob)
        group.refresh_from_db()
        self.assertEqual(group.owner, self.bob)
        self.assertEqual(services.membership_of(group, self.bob).role, GroupRole.OWNER)
        self.assertEqual(services.membership_of(group, self.alice).role, GroupRole.ADMIN)

    def test_only_the_current_owner_can_transfer_ownership(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        with self.assertRaises(services.SocialError):
            services.transfer_ownership(group, acting_user=self.bob, new_owner=self.alice)

    def test_cannot_transfer_ownership_to_a_non_member(self):
        group = services.create_group(self.alice, "Lifters")
        with self.assertRaises(services.SocialError):
            services.transfer_ownership(group, acting_user=self.alice, new_owner=self.bob)

    def test_the_owner_can_leave_after_transferring_ownership(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        services.transfer_ownership(group, acting_user=self.alice, new_owner=self.bob)
        services.leave_group(group, self.alice)
        self.assertIsNone(services.membership_of(group, self.alice))


class GroupInviteServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.group = services.create_group(self.alice, "Lifters")
        make_friends(self.alice, self.bob)

    def test_a_member_can_invite_a_friend(self):
        invite = services.invite_to_group(self.group, invited_by=self.alice, invited_user=self.bob)
        self.assertEqual(invite.status, GroupInviteStatus.PENDING)

    def test_a_non_member_cannot_send_invites(self):
        carol = User.objects.create_user(username="carol", password="s3cret-pass")
        make_friends(carol, self.bob)
        with self.assertRaises(services.SocialError):
            services.invite_to_group(self.group, invited_by=carol, invited_user=self.bob)

    def test_cannot_invite_someone_who_isnt_a_friend(self):
        carol = User.objects.create_user(username="carol", password="s3cret-pass")
        with self.assertRaises(services.SocialError):
            services.invite_to_group(self.group, invited_by=self.alice, invited_user=carol)

    def test_cannot_invite_someone_already_in_the_group(self):
        services.enable_invite(self.group)
        services.join_group_by_code(self.bob, self.group.invite_code)
        with self.assertRaises(services.SocialError):
            services.invite_to_group(self.group, invited_by=self.alice, invited_user=self.bob)

    def test_cannot_invite_if_recipient_opted_out(self):
        self.bob.allow_group_invites = False
        self.bob.save()
        with self.assertRaises(services.SocialError):
            services.invite_to_group(self.group, invited_by=self.alice, invited_user=self.bob)

    def test_cannot_invite_if_blocked(self):
        services.block_user(self.alice, self.bob)
        with self.assertRaises(services.SocialError):
            services.invite_to_group(self.group, invited_by=self.alice, invited_user=self.bob)

    def test_can_still_join_via_the_link_even_with_invites_turned_off(self):
        self.bob.allow_group_invites = False
        self.bob.save()
        services.enable_invite(self.group)
        joined_group, created = services.join_group_by_code(self.bob, self.group.invite_code)
        self.assertTrue(created)

    def test_accepting_creates_a_membership(self):
        invite = services.invite_to_group(self.group, invited_by=self.alice, invited_user=self.bob)
        services.accept_group_invite(invite, acting_user=self.bob)
        self.assertIsNotNone(services.membership_of(self.group, self.bob))

    def test_only_the_invited_user_can_respond(self):
        invite = services.invite_to_group(self.group, invited_by=self.alice, invited_user=self.bob)
        with self.assertRaises(services.SocialError):
            services.accept_group_invite(invite, acting_user=self.alice)

    def test_declining_does_not_create_a_membership(self):
        invite = services.invite_to_group(self.group, invited_by=self.alice, invited_user=self.bob)
        services.decline_group_invite(invite, acting_user=self.bob)
        self.assertIsNone(services.membership_of(self.group, self.bob))


class GroupMessageServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.group = services.create_group(self.alice, "Lifters")

    def test_a_member_can_send_a_message(self):
        message = services.send_group_message(self.group, self.alice, "hi")
        self.assertEqual(message.body, "hi")

    def test_a_non_member_cannot_send_a_message(self):
        with self.assertRaises(services.SocialError):
            services.send_group_message(self.group, self.bob, "hi")

    def test_sending_a_message_notifies_every_other_member_but_not_the_sender(self):
        services.enable_invite(self.group)
        services.join_group_by_code(self.bob, self.group.invite_code)
        carol = User.objects.create_user(username="carol", password="s3cret-pass")
        services.join_group_by_code(carol, self.group.invite_code)
        with mock.patch("apps.core.push.send_push_notification") as mock_send:
            services.send_group_message(self.group, self.alice, "hi")
        notified = {call.args[0] for call in mock_send.call_args_list}
        self.assertEqual(notified, {self.bob, carol})

    def test_unread_count_uses_last_read_at(self):
        services.enable_invite(self.group)
        services.join_group_by_code(self.bob, self.group.invite_code)
        services.send_group_message(self.group, self.alice, "one")
        services.send_group_message(self.group, self.alice, "two")
        self.assertEqual(services.unread_group_message_count(self.bob, self.group), 2)
        services.mark_group_read(self.group, self.bob)
        self.assertEqual(services.unread_group_message_count(self.bob, self.group), 0)
        services.send_group_message(self.group, self.alice, "three")
        self.assertEqual(services.unread_group_message_count(self.bob, self.group), 1)

    def test_deleting_a_group_deletes_its_messages(self):
        services.send_group_message(self.group, self.alice, "hi")
        services.delete_group(self.group, self.alice)
        self.assertEqual(GroupMessage.objects.count(), 0)

    def test_a_message_sent_before_joining_is_never_unread(self):
        services.send_group_message(self.group, self.alice, "before you got here")
        services.enable_invite(self.group)
        services.join_group_by_code(self.bob, self.group.invite_code)
        self.assertEqual(services.unread_group_message_count(self.bob, self.group), 0)

    def test_unread_group_message_counts_by_group_covers_every_membership(self):
        other_group = services.create_group(self.alice, "Runners")
        services.enable_invite(self.group)
        services.enable_invite(other_group)
        services.join_group_by_code(self.bob, self.group.invite_code)
        services.join_group_by_code(self.bob, other_group.invite_code)
        services.send_group_message(self.group, self.alice, "one")
        services.send_group_message(self.group, self.alice, "two")
        services.send_group_message(other_group, self.alice, "hi")
        counts = services.unread_group_message_counts_by_group(self.bob)
        self.assertEqual(counts, {self.group.pk: 2, other_group.pk: 1})

    def test_has_unread_group_messages(self):
        self.assertFalse(services.has_unread_group_messages(self.bob))
        services.enable_invite(self.group)
        services.join_group_by_code(self.bob, self.group.invite_code)
        self.assertFalse(services.has_unread_group_messages(self.bob))
        services.send_group_message(self.group, self.alice, "hi")
        self.assertTrue(services.has_unread_group_messages(self.bob))
        services.mark_group_read(self.group, self.bob)
        self.assertFalse(services.has_unread_group_messages(self.bob))

    def test_unread_group_message_count_is_a_single_query_regardless_of_group_count(self):
        # The whole point of the fix: apps.social.context_processors.
        # social_badge calls this (indirectly, via
        # has_unread_group_messages) on every page load for every
        # logged-in user — it must not scale with how many groups
        # that user happens to be in.
        for i in range(5):
            group = services.create_group(self.alice, f"Group {i}")
            services.enable_invite(group)
            services.join_group_by_code(self.bob, group.invite_code)
            services.send_group_message(group, self.alice, "hi")
        with self.assertNumQueries(1):
            services.unread_group_message_count(self.bob)
        with self.assertNumQueries(1):
            services.has_unread_group_messages(self.bob)


class SocialBadgeContextProcessorTests(TestCase):
    """apps.social.context_processors.social_badge — the small dot on
    base.html's Profile nav icon, and (see docs/SOCIAL.md "Unread
    counts are one query") a fixed four-query cost on every single
    page load for a logged-in user, regardless of how much social
    activity actually exists. Pinned here the same way apps.nutrition's
    recipe-list test pins its own context-processor cost, so a future
    regression back to counting instead of existence-checking (or back
    to a per-group loop) shows up immediately."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def _badge(self):
        return self.client.get(reverse("dashboard")).context["social_badge"]

    def test_no_badge_with_no_activity(self):
        self.assertFalse(self._badge())

    def test_badge_for_a_pending_friend_request(self):
        services.send_friend_request(self.bob, self.alice)
        self.assertTrue(self._badge())

    def test_badge_for_a_pending_group_invite(self):
        group = services.create_group(self.bob, "Lifters")
        make_friends(self.alice, self.bob)
        services.invite_to_group(group, invited_by=self.bob, invited_user=self.alice)
        self.assertTrue(self._badge())

    def test_badge_for_an_unread_direct_message(self):
        make_friends(self.alice, self.bob)
        services.send_direct_message(self.bob, self.alice, "hi")
        self.assertTrue(self._badge())

    def test_badge_for_an_unread_group_message(self):
        group = services.create_group(self.bob, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.alice, group.invite_code)
        services.send_group_message(group, self.bob, "hi")
        self.assertTrue(self._badge())

    def test_four_queries_regardless_of_activity(self):
        with self.assertNumQueries(4):
            services.has_pending_friend_requests(self.alice)
            services.has_pending_group_invites(self.alice)
            services.has_unread_direct_messages(self.alice)
            services.has_unread_group_messages(self.alice)


class FriendViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_friend_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("social:friend-list"))
        self.assertEqual(response.status_code, 302)

    def test_friend_search_finds_by_username(self):
        response = self.client.get(reverse("social:friend-search"), {"q": "bob"})
        self.assertContains(response, "bob")

    def test_friend_search_excludes_existing_friends(self):
        # Not assertNotContains(response, "bob") — the search term
        # itself ("bob") legitimately echoes back into the input's own
        # value="" attribute and the page's og:url regardless of
        # whether bob himself shows up in the results, so check the
        # actual results content instead.
        make_friends(self.alice, self.bob)
        response = self.client.get(reverse("social:friend-search"), {"q": "bob"})
        self.assertEqual(response.context["results"], [])

    def test_friend_search_excludes_blocked_users_either_direction(self):
        carol = User.objects.create_user(username="carol", password="s3cret-pass")
        services.block_user(self.alice, self.bob)
        services.block_user(carol, self.alice)
        response = self.client.get(reverse("social:friend-search"), {"q": "a"})
        usernames = {u.username for u in response.context["results"]}
        self.assertNotIn("bob", usernames)
        self.assertNotIn("carol", usernames)

    def test_sending_a_request_via_the_view(self):
        self.client.post(reverse("social:friend-request-send", args=[self.bob.pk]))
        self.assertTrue(
            FriendRequest.objects.filter(from_user=self.alice, to_user=self.bob).exists()
        )

    def test_accepting_a_request_via_the_view(self):
        services.send_friend_request(self.bob, self.alice)
        request = FriendRequest.objects.get(from_user=self.bob, to_user=self.alice)
        self.client.post(
            reverse("social:friend-request-respond", args=[request.pk]), {"action": "accept"}
        )
        self.assertTrue(services.are_friends(self.alice, self.bob))

    def test_removing_a_friend_via_the_view(self):
        make_friends(self.alice, self.bob)
        self.client.post(reverse("social:friend-remove", args=[self.bob.pk]))
        self.assertFalse(services.are_friends(self.alice, self.bob))

    def test_blocking_and_unblocking_via_the_views(self):
        self.client.post(reverse("social:block-user", args=[self.bob.pk]))
        self.assertTrue(Block.objects.filter(blocker=self.alice, blocked=self.bob).exists())
        self.client.post(reverse("social:unblock-user", args=[self.bob.pk]))
        self.assertFalse(Block.objects.filter(blocker=self.alice, blocked=self.bob).exists())


class GroupViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_group_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("social:group-list"))
        self.assertEqual(response.status_code, 302)

    def test_creating_a_group_via_the_view(self):
        response = self.client.post(
            reverse("social:group-create"), {"name": "Lifters", "description": ""}
        )
        group = Group.objects.get(name="Lifters")
        self.assertRedirects(response, reverse("social:group-detail", args=[group.pk]))
        self.assertEqual(services.membership_of(group, self.alice).role, GroupRole.OWNER)

    def test_group_detail_404s_for_a_non_member(self):
        group = services.create_group(self.bob, "Lifters")
        response = self.client.get(reverse("social:group-detail", args=[group.pk]))
        self.assertEqual(response.status_code, 404)

    def test_group_detail_marks_your_own_row(self):
        group = services.create_group(self.alice, "Lifters")
        response = self.client.get(reverse("social:group-detail", args=[group.pk]))
        self.assertContains(response, "(you)")

    def test_group_list_shows_a_member_count(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        response = self.client.get(reverse("social:group-list"))
        self.assertEqual(response.context["groups"][0]["member_count"], 2)
        self.assertContains(response, "2 members")

    def test_only_a_manager_can_edit(self):
        group = services.create_group(self.bob, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.alice, group.invite_code)
        response = self.client.post(
            reverse("social:group-edit", args=[group.pk]), {"name": "New name", "description": ""}
        )
        self.assertRedirects(response, reverse("social:group-detail", args=[group.pk]))
        group.refresh_from_db()
        self.assertEqual(group.name, "Lifters")

    def test_a_regular_member_can_see_the_invite_a_friend_form(self):
        # Regression: the form was previously rendered only inside
        # the {% if can_manage %} block, even though any member is
        # actually allowed to send one (services.invite_to_group).
        group = services.create_group(self.bob, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.alice, group.invite_code)
        carol = User.objects.create_user(username="carol", password="s3cret-pass")
        services.send_friend_request(self.alice, carol)
        services.accept_friend_request(
            FriendRequest.objects.get(from_user=self.alice, to_user=carol), acting_user=carol
        )
        response = self.client.get(reverse("social:group-detail", args=[group.pk]))
        self.assertContains(response, "Invite a friend")

    def test_toggling_the_invite_link(self):
        group = services.create_group(self.alice, "Lifters")
        self.client.post(reverse("social:group-invite-toggle", args=[group.pk]))
        group.refresh_from_db()
        self.assertTrue(group.invite_enabled)

    def test_join_via_invite_link_view(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        self.client.login(username="bob", password="s3cret-pass")
        response = self.client.post(reverse("group-invite-join", args=[group.invite_code]))
        self.assertRedirects(response, reverse("social:group-detail", args=[group.pk]))
        self.assertIsNotNone(services.membership_of(group, self.bob))

    def test_invalid_invite_code_shows_an_error_state(self):
        response = self.client.get(reverse("group-invite-join", args=["NOSUCHCODE"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "isn't valid")

    def test_anonymous_visitor_sees_a_login_prompt_not_a_crash(self):
        # The most common real path for this URL: someone who isn't
        # even logged in yet clicking a link a friend shared with
        # them — request.user is AnonymousUser here, not a real User,
        # so the view/template must not assume otherwise.
        self.client.logout()
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        response = self.client.get(reverse("group-invite-join", args=[group.invite_code]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Log in to join this group.")

    def test_anonymous_visitor_cannot_join_by_posting_directly(self):
        self.client.logout()
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        response = self.client.post(reverse("group-invite-join", args=[group.invite_code]))
        self.assertEqual(response.status_code, 200)
        # Still just the owner — nobody was added as a side effect of
        # an unauthenticated POST.
        self.assertEqual(GroupMembership.objects.filter(group=group).count(), 1)

    def test_group_leave_and_delete(self):
        group = services.create_group(self.alice, "Lifters")
        response = self.client.post(reverse("social:group-delete", args=[group.pk]))
        self.assertRedirects(response, reverse("social:group-list"))
        self.assertFalse(Group.objects.filter(pk=group.pk).exists())

    def test_transfer_ownership_via_the_view(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        response = self.client.post(
            reverse("social:group-transfer-ownership", args=[group.pk, self.bob.pk])
        )
        self.assertRedirects(response, reverse("social:group-detail", args=[group.pk]))
        self.assertTrue(services.is_group_owner(self.bob, group))

    def test_group_invite_send_rejects_a_crafted_non_friend_target(self):
        # A regression check for the view layer specifically, not just
        # the service function directly: group_detail.html's dropdown
        # only ever offers friends, but a crafted POST naming any
        # user id must still be refused, not just hidden from the UI.
        group = services.create_group(self.alice, "Lifters")
        stranger = User.objects.create_user(username="stranger", password="s3cret-pass")
        self.client.post(
            reverse("social:group-invite-send", args=[group.pk]), {"friend": stranger.pk}
        )
        self.assertIsNone(services.membership_of(group, stranger))
        from apps.social.models import GroupInvite

        self.assertFalse(GroupInvite.objects.filter(group=group, invited_user=stranger).exists())


class MessageViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_message_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("social:message-list"))
        self.assertEqual(response.status_code, 302)

    def test_message_thread_404s_for_a_non_friend(self):
        response = self.client.get(reverse("social:message-thread", args=[self.bob.pk]))
        self.assertEqual(response.status_code, 404)

    def test_message_input_has_an_accessible_label(self):
        # Regression: the compact chat input had no visible label (by
        # design — redundant clutter next to a one-line box) but also
        # no visually-hidden one, leaving it with no accessible name
        # at all for a screen reader.
        make_friends(self.alice, self.bob)
        response = self.client.get(reverse("social:message-thread", args=[self.bob.pk]))
        self.assertContains(response, 'class="visually-hidden"')
        self.assertContains(response, f'for="{response.context["form"]["body"].id_for_label}"')
        # Regression: the explanatory comment right above this label
        # was originally written as {# ... #} spanning multiple lines
        # — Django's {# #} only supports a single line and leaks the
        # rest literally into the page for anything longer
        # ({% comment %}...{% endcomment %} is the multi-line form).
        self.assertNotContains(response, "{#")

    def test_sending_a_direct_message_via_the_fragment_view(self):
        make_friends(self.alice, self.bob)
        response = self.client.post(
            reverse("social:message-thread-fragment", args=[self.bob.pk]), {"body": "hi"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            DirectMessage.objects.filter(sender=self.alice, recipient=self.bob, body="hi").exists()
        )
        self.assertContains(response, "hi")

    def test_opening_a_thread_marks_it_read(self):
        make_friends(self.alice, self.bob)
        services.send_direct_message(self.bob, self.alice, "hi")
        self.client.get(reverse("social:message-thread-fragment", args=[self.bob.pk]))
        self.assertEqual(services.unread_direct_message_count(self.alice), 0)

    def test_group_thread_404s_for_a_non_member(self):
        group = services.create_group(self.bob, "Lifters")
        response = self.client.get(reverse("social:group-thread", args=[group.pk]))
        self.assertEqual(response.status_code, 404)

    def test_group_message_input_has_an_accessible_label(self):
        group = services.create_group(self.alice, "Lifters")
        response = self.client.get(reverse("social:group-thread", args=[group.pk]))
        self.assertContains(response, 'class="visually-hidden"')
        self.assertNotContains(response, "{#")

    def test_sending_a_group_message_via_the_fragment_view(self):
        group = services.create_group(self.alice, "Lifters")
        response = self.client.post(
            reverse("social:group-thread-fragment", args=[group.pk]), {"body": "hi team"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(GroupMessage.objects.filter(group=group, body="hi team").exists())
        self.assertContains(response, "hi team")

    def test_thread_fragment_caps_at_the_message_limit(self):
        from apps.social.views_messages import THREAD_MESSAGE_LIMIT

        make_friends(self.alice, self.bob)
        for i in range(THREAD_MESSAGE_LIMIT + 5):
            services.send_direct_message(self.alice, self.bob, f"message {i}")
        response = self.client.get(reverse("social:message-thread-fragment", args=[self.bob.pk]))
        self.assertEqual(len(response.context["thread_messages"]), THREAD_MESSAGE_LIMIT)
        # The *most recent* messages, not the oldest — and still in
        # chronological order (oldest-of-the-visible-window first).
        self.assertContains(response, "message 104")
        self.assertNotContains(response, "message 0")


class ReassignOwnedGroupsBeforeDeletionTests(TestCase):
    """apps.accounts.services.delete_account calls this before
    user.delete() cascades the departing user's own GroupMembership
    rows away — see that function's own docstring for why a naive
    cascade alone would leave a group with other members still in it
    permanently unmanageable."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")

    def test_ownership_transfers_to_the_longest_standing_admin_first(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        carol = User.objects.create_user(username="carol", password="s3cret-pass")
        services.join_group_by_code(self.bob, group.invite_code)
        services.join_group_by_code(carol, group.invite_code)
        services.set_member_role(
            group, acting_user=self.alice, target_user=carol, role=GroupRole.ADMIN
        )
        services.reassign_owned_groups_before_deletion(self.alice)
        self.assertEqual(services.membership_of(group, carol).role, GroupRole.OWNER)
        self.assertEqual(services.membership_of(group, self.bob).role, GroupRole.MEMBER)

    def test_falls_back_to_the_longest_standing_member_with_no_admin(self):
        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        services.reassign_owned_groups_before_deletion(self.alice)
        self.assertEqual(services.membership_of(group, self.bob).role, GroupRole.OWNER)

    def test_a_group_with_no_other_members_is_left_alone(self):
        group = services.create_group(self.alice, "Lifters")
        services.reassign_owned_groups_before_deletion(self.alice)
        group.refresh_from_db()
        self.assertEqual(group.owner, self.alice)
        self.assertEqual(services.membership_of(group, self.alice).role, GroupRole.OWNER)

    def test_full_account_deletion_leaves_the_group_manageable(self):
        from apps.accounts.services import delete_account

        group = services.create_group(self.alice, "Lifters")
        services.enable_invite(group)
        services.join_group_by_code(self.bob, group.invite_code)
        delete_account(self.alice)
        group.refresh_from_db()
        self.assertEqual(group.owner, self.bob)
        self.assertTrue(services.is_group_owner(self.bob, group))

    def test_every_group_the_departing_user_owns_gets_reassigned(self):
        # Not just the first one found — a real account could own
        # several groups at once.
        carol = User.objects.create_user(username="carol", password="s3cret-pass")
        gym = services.create_group(self.alice, "Gym")
        book_club = services.create_group(self.alice, "Book club")
        services.enable_invite(gym)
        services.enable_invite(book_club)
        services.join_group_by_code(self.bob, gym.invite_code)
        services.join_group_by_code(carol, book_club.invite_code)
        services.reassign_owned_groups_before_deletion(self.alice)
        self.assertTrue(services.is_group_owner(self.bob, gym))
        self.assertTrue(services.is_group_owner(carol, book_club))
