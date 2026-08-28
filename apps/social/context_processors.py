"""Global template context — the small dot badge on base.html's
Profile nav icon needs to appear on every page, the same reasoning
apps.workouts.context_processors.active_workout_session already
follows for its own floating training button.
"""

from . import services


def social_badge(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    has_activity = (
        services.has_pending_friend_requests(user)
        or services.has_pending_group_invites(user)
        or services.has_unread_direct_messages(user)
        or services.has_unread_group_messages(user)
    )
    return {"social_badge": has_activity}
