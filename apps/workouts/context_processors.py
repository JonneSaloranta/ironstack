"""Global template context — not app-specific view context — because
`base.html`'s floating "go to training mode" button (see
docs/UI.md "Training mode") has to appear on every page, not just the
workout ones, whenever the logged-in user has a workout in progress.
"""

from .models import WorkoutSessionStatus
from .services import sessions_for


def active_workout_session(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    session = (
        sessions_for(user)
        .filter(status=WorkoutSessionStatus.IN_PROGRESS)
        .select_related("workout")
        .first()
    )
    return {"active_workout_session": session}
