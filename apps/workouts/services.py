"""Workout session domain logic, kept out of views per CLAUDE.md.

This is where the snapshot-on-start mechanism (docs/ARCHITECTURE.md
"Historical integrity mechanism") actually happens: starting a session
copies each prescription's planned values onto the session's own
PerformedExercise rows so later edits to the program can never rewrite
what this session says happened.
"""

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import ExerciseSet, PerformedExercise, WorkoutSession, WorkoutSessionStatus


def sessions_for(user):
    """A user's own workout sessions only — never shared, unlike programs."""
    return WorkoutSession.objects.filter(user=user)


@transaction.atomic
def start_session(user, workout=None):
    """Start a session, snapshotting `workout`'s prescriptions if given.

    `workout=None` starts a freeform session with no planned exercises —
    the user adds exercises as they go via `add_performed_exercise`.
    """
    session = WorkoutSession.objects.create(
        user=user,
        workout=workout,
        program=workout.program if workout else None,
        status=WorkoutSessionStatus.IN_PROGRESS,
        started_at=timezone.now(),
    )
    if workout is not None:
        performed_exercises = [
            PerformedExercise(
                session=session,
                exercise=prescription.exercise,
                prescription=prescription,
                order=prescription.order,
                set_count=prescription.set_count,
                min_reps=prescription.min_reps,
                max_reps=prescription.max_reps,
                target_weight=prescription.target_weight,
                target_rpe=prescription.target_rpe,
                target_rir=prescription.target_rir,
                progression_method=prescription.progression_method,
                weight_increment=prescription.weight_increment,
                notes=prescription.notes,
            )
            for prescription in workout.prescriptions.order_by("order", "id")
        ]
        PerformedExercise.objects.bulk_create(performed_exercises)
    return session


def complete_session(session):
    session.status = WorkoutSessionStatus.COMPLETED
    session.ended_at = timezone.now()
    session.save(update_fields=["status", "ended_at", "updated_at"])
    return session


def abandon_session(session):
    session.status = WorkoutSessionStatus.ABANDONED
    session.ended_at = timezone.now()
    session.save(update_fields=["status", "ended_at", "updated_at"])
    return session


def add_performed_exercise(session, exercise):
    """Add an exercise to an in-progress session with no prescription
    behind it — used for freeform sessions and for going off-plan mid
    workout (the user always keeps the option to deviate)."""
    next_order = (
        session.performed_exercises.aggregate(highest=Max("order"))["highest"] or -1
    ) + 1
    return PerformedExercise.objects.create(
        session=session, exercise=exercise, order=next_order
    )


def default_set_values(performed_exercise):
    """Best starting guess for a new set's weight/reps: repeat the most
    recently logged set in this exercise, falling back to the snapshotted
    target. Pure UX convenience (fewer taps), not a progression decision —
    the user can always change it before saving."""
    last_set = performed_exercise.sets.order_by("-set_number").first()
    if last_set is not None:
        return {"weight": last_set.weight, "reps": last_set.reps}
    return {
        "weight": performed_exercise.target_weight,
        "reps": performed_exercise.min_reps,
    }


def log_set(performed_exercise, **fields):
    next_number = performed_exercise.sets.count() + 1
    return ExerciseSet.objects.create(
        performed_exercise=performed_exercise, set_number=next_number, **fields
    )


def is_performed_exercise_complete(performed_exercise):
    """Whether a performed exercise has nothing obviously left to log —
    the definition training mode's "what's next" stepper (see
    `first_incomplete_performed_exercise`) is built on. A prescribed
    exercise (`set_count` snapshotted from the plan) is complete once it
    has that many sets; a freeform/ad-hoc addition has no target to
    compare against, so it's treated as complete once it has at least one
    set — there's no way to know a user wants a second set on an
    unplanned exercise until they say so via `train-set-log`'s "add
    another set" affordance, which stays available regardless of this.

    Reads `.sets.all()` (not `.count()`) so a caller that already
    prefetched `performed_exercises__sets` doesn't trigger an extra query
    per exercise.
    """
    set_total = len(performed_exercise.sets.all())
    if performed_exercise.set_count:
        return set_total >= performed_exercise.set_count
    return set_total >= 1


def first_incomplete_performed_exercise(performed_exercises):
    """The first performed exercise (in program order) that still has
    sets left to log — training mode's default "current exercise", per
    `is_performed_exercise_complete`. `None` once every exercise in the
    list is done."""
    for performed_exercise in performed_exercises:
        if not is_performed_exercise_complete(performed_exercise):
            return performed_exercise
    return None
