"""Global template context — the "My clients" settings-row on the
Profile page (templates/accounts/profile.html) needs its own pending-
request badge on every page that row appears on, the same reasoning
apps.social.context_processors.social_badge already follows for its
own Profile nav badge.
"""

from . import services


def pending_coaching_activity(request):
    """Only a coach can have *incoming* coaching requests to badge —
    a coachee's own outgoing pending requests aren't a "you need to
    act" signal, matching apps.social's own one-directional
    has_pending_friend_requests shape."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_personal_trainer:
        return {"coaching_badge": False}
    return {"coaching_badge": services.has_pending_coaching_requests(user)}
