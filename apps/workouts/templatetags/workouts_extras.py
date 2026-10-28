"""Template filters for training mode (templates/workouts/_train_panel.html)
- thin wrappers around apps.workouts.services so the progress-stepper
markup doesn't have to reimplement "is this exercise done?" in template
logic (CLAUDE.md: no business logic in templates).
"""

from django import template

from apps.workouts import services, set_timing

register = template.Library()


@register.filter
def is_complete(performed_exercise):
    return services.is_performed_exercise_complete(performed_exercise)


@register.filter
def workout_duration_estimate(workout):
    """apps.workouts.set_timing.estimate_workout for a program's workout
    card (templates/programs/program_detail.html)."""
    return set_timing.estimate_workout(workout)


@register.inclusion_tag("workouts/_exercise_set_cycle.html", takes_context=True)
def exercise_set_cycle(context, exercise):
    """The exercise detail page's "Set cycle" card - a workouts tag so
    apps.exercises keeps not depending on apps.workouts. `everyone` is
    left out when only the user's own sets are behind it (it would just
    repeat their own figure)."""
    user = context["request"].user
    own = set_timing.own_cycle(user, exercise)
    everyone = set_timing.instance_cycle(exercise)
    if everyone is not None and everyone.user_count < 2:
        everyone = None
    return {"own": own, "everyone": everyone, "min_own": set_timing.MIN_OWN_SAMPLES}
