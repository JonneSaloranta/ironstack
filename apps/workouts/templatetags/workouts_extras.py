"""Template filters for training mode (templates/workouts/_train_panel.html)
— thin wrappers around apps.workouts.services so the progress-stepper
markup doesn't have to reimplement "is this exercise done?" in template
logic (CLAUDE.md: no business logic in templates).
"""

from django import template

from apps.workouts import services

register = template.Library()


@register.filter
def is_complete(performed_exercise):
    return services.is_performed_exercise_complete(performed_exercise)
