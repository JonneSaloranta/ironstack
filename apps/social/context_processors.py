"""Global template context — the small dot badge on base.html's
Profile nav icon needs to appear on every page, the same reasoning
apps.workouts.context_processors.active_workout_session already
follows for its own floating training button.
"""

from . import services


def social_badge(request):
    """Two context keys from one pass over the same underlying
    booleans, not two separate context processors each re-running its
    own `.exists()` queries for the same thing: `social_badge` (the
    small dot on the Profile nav icon — friend requests, group
    invites, or unread messages, any of the four) and
    `has_unread_messages` (narrower — messages only, not friend
    requests/group invites), which gates base.html's own floating
    `.messages-fab` button (the same "reachable from any page"
    treatment apps.workouts.context_processors.active_workout_session
    already gives a workout in progress) — it should only appear for
    the thing it's actually about, not any pending social activity."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    has_unread_dms = services.has_unread_direct_messages(user)
    has_unread_group_messages = services.has_unread_group_messages(user)
    has_unread = has_unread_dms or has_unread_group_messages
    has_activity = (
        has_unread
        or services.has_pending_friend_requests(user)
        or services.has_pending_group_invites(user)
    )
    return {"social_badge": has_activity, "has_unread_messages": has_unread}
