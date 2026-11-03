from django import template

from apps.stretching import services
from apps.workouts.models import WorkoutSessionStatus

register = template.Library()


@register.filter
def clock(seconds):
    """Whole seconds as a stopwatch-style "m:ss" (90 → "1:30") - the
    unit hold times and routine lengths are planned in, where rounding
    to whole minutes (core_extras.duration) would hide the detail."""
    if seconds is None:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


@register.inclusion_tag("stretching/_cooldown_card.html", takes_context=True)
def cooldown_card(context, workout_session):
    """The "cool down with a stretch" card on a finished workout's page
    (templates/workouts/session_detail.html). An inclusion tag rather
    than extra context from apps.workouts' view, so workouts never has
    to import stretching - the dependency stays one-directional
    (stretching knows about workouts, not the other way round)."""
    request = context["request"]
    user = request.user
    empty = {"show": False}
    if not getattr(user, "stretching_enabled", False) or workout_session.user_id != user.id:
        return empty
    if workout_session.status != WorkoutSessionStatus.COMPLETED:
        return empty
    done = services.cooldown_done_after(workout_session)
    if done is not None:
        return {"show": True, "done": done, "request": request}
    suggestion = services.suggest_cooldown(workout_session)
    if suggestion is None:
        return empty
    seconds = services.cooldown_seconds(suggestion)
    return {
        "show": True,
        "suggestion": suggestion,
        "seconds": seconds,
        "workout_session": workout_session,
        "request": request,
    }
