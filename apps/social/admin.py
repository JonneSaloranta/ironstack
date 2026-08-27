from django.contrib import admin

from .models import (
    Block,
    DirectMessage,
    FriendRequest,
    Friendship,
    Group,
    GroupInvite,
    GroupMembership,
    GroupMessage,
)


class GroupMembershipInline(admin.TabularInline):
    model = GroupMembership
    extra = 0


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "invite_enabled", "created_at"]
    list_filter = ["invite_enabled"]
    search_fields = ["name", "owner__username"]
    inlines = [GroupMembershipInline]


@admin.register(FriendRequest)
class FriendRequestAdmin(admin.ModelAdmin):
    list_display = ["from_user", "to_user", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["from_user__username", "to_user__username"]


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ["user_low", "user_high", "created_at"]
    search_fields = ["user_low__username", "user_high__username"]


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    list_display = ["blocker", "blocked", "created_at"]
    search_fields = ["blocker__username", "blocked__username"]


@admin.register(GroupInvite)
class GroupInviteAdmin(admin.ModelAdmin):
    list_display = ["group", "invited_user", "invited_by", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["group__name", "invited_user__username"]


@admin.register(DirectMessage)
class DirectMessageAdmin(admin.ModelAdmin):
    # Django admin's own delete action is this app's moderation tool —
    # no bespoke reporting UI was asked for (see apps/social's plan
    # notes on scope).
    list_display = ["sender", "recipient", "created_at"]
    search_fields = ["sender__username", "recipient__username", "body"]


@admin.register(GroupMessage)
class GroupMessageAdmin(admin.ModelAdmin):
    list_display = ["sender", "group", "created_at"]
    search_fields = ["sender__username", "group__name", "body"]
