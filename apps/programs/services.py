"""Program domain logic kept out of views, per CLAUDE.md.

Visibility rules and template copying live here so later phases (workout
logging) can reuse the same "which programs can this user use" query
instead of re-deriving it.
"""

from django.db import transaction
from django.db.models import Q

from .models import ExercisePrescription, Program, Workout


def visible_to(user):
    """Programs a user may view/use: their own + system templates."""
    return Program.objects.filter(Q(owner=user) | Q(owner__isnull=True))


def editable_by(user):
    """Programs a user may edit/delete: their own only.

    System templates (owner is null) are read-only — copy them instead.
    """
    return Program.objects.filter(owner=user)


@transaction.atomic
def copy_program(source: Program, owner) -> Program:
    """Deep-copy a program (workouts + prescriptions) into a new program
    owned by `owner`, ready to edit/schedule independently of the source.
    """
    copy = Program.objects.create(
        owner=owner,
        name=source.name,
        description=source.description,
        is_template=False,
    )
    for workout in source.workouts.order_by("order", "id"):
        workout_copy = Workout.objects.create(
            program=copy,
            name=workout.name,
            order=workout.order,
            scheduled_weekday=workout.scheduled_weekday,
            notes=workout.notes,
        )
        prescriptions = [
            ExercisePrescription(
                workout=workout_copy,
                exercise=prescription.exercise,
                order=prescription.order,
                set_count=prescription.set_count,
                min_reps=prescription.min_reps,
                max_reps=prescription.max_reps,
                target_weight=prescription.target_weight,
                target_rpe=prescription.target_rpe,
                target_rir=prescription.target_rir,
                progression_method=prescription.progression_method,
                weight_increment=prescription.weight_increment,
                percentage_target=prescription.percentage_target,
                notes=prescription.notes,
            )
            for prescription in workout.prescriptions.order_by("order", "id")
        ]
        ExercisePrescription.objects.bulk_create(prescriptions)
    return copy
