"""Workout session domain logic, kept out of views per CLAUDE.md.

This is where the snapshot-on-start mechanism (docs/ARCHITECTURE.md
"Historical integrity mechanism") actually happens: starting a session
copies each prescription's planned values onto the session's own
PerformedExercise rows so later edits to the program can never rewrite
what this session says happened.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import (
    ExerciseSet,
    PerformedExercise,
    RestTimerNotification,
    WorkoutSession,
    WorkoutSessionStatus,
)

#: The RPE scale (rate of perceived exertion) a logged or prescribed
#: value must fall on - anything else is a typo, not an effort level.
RPE_MIN = 1
RPE_MAX = 10


def validate_rpe(value):
    """Raises django ValidationError for an RPE outside RPE_MIN..RPE_MAX."""
    from django.core.exceptions import ValidationError
    from django.utils.translation import gettext

    if value is not None and not RPE_MIN <= value <= RPE_MAX:
        raise ValidationError(
            gettext("RPE goes from %(min)s to %(max)s.") % {"min": RPE_MIN, "max": RPE_MAX}
        )


class SessionNotInProgress(Exception):
    """Raised when completing/abandoning a session that already ended.

    A finished session is history: flipping a completed one to abandoned
    (or re-completing it) would rewrite its status and `ended_at` after
    the fact and silently move it in or out of analytics and streaks.
    """


def sessions_for(user):
    """A user's own workout sessions only - never shared, unlike programs."""
    return WorkoutSession.objects.filter(user=user)


@dataclass(frozen=True)
class UpNext:
    """The workout that's next in a program. `day` is the date it's
    scheduled for (a weekly program), or None for a rotation, which is
    simply next whenever the user trains."""

    program: object
    workout: object
    day: date | None = None


def next_workout(user, program, today):
    """The workout `user` has next in `program`, or None if it has none.

    A weekly program (not `is_continuous`, at least one workout with a
    `scheduled_weekday`) offers today's workout - or, if today has none
    or it's already been done today, the next scheduled day's. Anything
    else is a rotation: the workout after the one the user last
    completed from this program, wrapping around, starting with the
    first. Only a suggestion: the user can still start any workout."""
    workouts = list(program.workouts.order_by("order", "id"))
    if not workouts:
        return None
    completed = sessions_for(user).filter(
        program=program, status=WorkoutSessionStatus.COMPLETED
    )
    scheduled = [w for w in workouts if w.scheduled_weekday is not None]
    if not program.is_continuous and scheduled:
        start = 1 if completed.filter(started_at__date=today).exists() else 0
        for offset in range(start, start + 7):
            day = today + timedelta(days=offset)
            for workout in scheduled:
                if workout.scheduled_weekday == day.weekday():
                    return UpNext(program, workout, day)
    last_workout_id = (
        completed.filter(workout__isnull=False)
        .order_by("-started_at")
        .values_list("workout_id", flat=True)
        .first()
    )
    ids = [w.id for w in workouts]
    if last_workout_id in ids:
        return UpNext(program, workouts[(ids.index(last_workout_id) + 1) % len(workouts)])
    return UpNext(program, workouts[0])


def up_next(user, today):
    """What the dashboard offers next: next_workout() in the program the
    user last completed a workout from, as long as they can still see
    it. None if they haven't trained from a program yet."""
    from apps.programs.services import visible_to

    session = (
        sessions_for(user)
        .filter(status=WorkoutSessionStatus.COMPLETED, program__isnull=False)
        .select_related("program")
        .first()
    )
    if session is None or not visible_to(user).filter(pk=session.program_id).exists():
        return None
    return next_workout(user, session.program, today)


#: How recent an identical in-progress session must be for
#: start_session_once() to treat a second start as the same tap.
DUPLICATE_START_WINDOW = timedelta(seconds=10)


@transaction.atomic
def start_session_once(user, workout=None):
    """start_session() for a user pressing "Start": a second press of the
    same Start button a moment later (a double tap, a re-sent form)
    returns the session the first one started instead of opening a
    second, empty one next to it. The user row lock makes two
    simultaneous presses take turns."""
    from django.contrib.auth import get_user_model

    get_user_model().objects.select_for_update().filter(pk=user.pk).first()
    recent = (
        sessions_for(user)
        .filter(
            status=WorkoutSessionStatus.IN_PROGRESS,
            workout=workout,
            started_at__gte=timezone.now() - DUPLICATE_START_WINDOW,
        )
        .order_by("-started_at")
        .first()
    )
    return recent or start_session(user, workout=workout)


@transaction.atomic
def start_session(user, workout=None):
    """Start a session, snapshotting `workout`'s prescriptions if given.

    `workout=None` starts a freeform session with no planned exercises -
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
                exercise_name=prescription.exercise.name,
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
    if not session.is_in_progress:
        raise SessionNotInProgress
    session.status = WorkoutSessionStatus.COMPLETED
    session.ended_at = timezone.now()
    session.save(update_fields=["status", "ended_at", "updated_at"])
    return session


def abandon_session(session):
    if not session.is_in_progress:
        raise SessionNotInProgress
    session.status = WorkoutSessionStatus.ABANDONED
    session.ended_at = timezone.now()
    session.save(update_fields=["status", "ended_at", "updated_at"])
    return session


def add_performed_exercise(session, exercise):
    """Add an exercise to an in-progress session with no prescription
    behind it - used for freeform sessions and for going off-plan mid
    workout (the user always keeps the option to deviate)."""
    # Not `(highest or -1) + 1`: a highest order of 0 is falsy, which
    # used to give the second added exercise order 0 as well.
    highest = session.performed_exercises.aggregate(highest=Max("order"))["highest"]
    next_order = 0 if highest is None else highest + 1
    return PerformedExercise.objects.create(
        session=session, exercise=exercise, exercise_name=exercise.name, order=next_order
    )


def default_set_values(performed_exercise):
    """Best starting guess for a new set's weight/reps: repeat the most
    recently logged set in this exercise, falling back to the snapshotted
    target. Pure UX convenience (fewer taps), not a progression decision -
    the user can always change it before saving."""
    last_set = performed_exercise.sets.order_by("-set_number").first()
    if last_set is not None:
        return {"weight": last_set.weight, "reps": last_set.reps}
    return {
        "weight": performed_exercise.target_weight,
        "reps": performed_exercise.min_reps,
    }


def last_performance(user, exercise):
    """The `PerformedExercise` (sets prefetched) of `exercise` in `user`'s
    most recent completed session that logged at least one set of it -
    "last time: 3 x 5 @ 80 kg" - or None. Warmups are included; their
    `is_warmup` tells them apart."""
    return (
        PerformedExercise.objects.filter(
            exercise=exercise,
            session__user=user,
            session__status=WorkoutSessionStatus.COMPLETED,
            sets__isnull=False,  # the join can repeat a row; .first() doesn't mind
        )
        .select_related("session")
        .prefetch_related("sets")
        .order_by("-session__started_at", "-id")
        .first()
    )


@transaction.atomic
def log_set(performed_exercise, **fields):
    """Log the next set, numbered one past the highest existing number.

    Not `count() + 1`: after a set is deleted that would reuse a number
    still held by a later set. The row lock makes two concurrent logs
    (a double tap) take turns instead of both reading the same maximum.
    """
    PerformedExercise.objects.select_for_update().filter(pk=performed_exercise.pk).first()
    highest = performed_exercise.sets.aggregate(highest=Max("set_number"))["highest"]
    return ExerciseSet.objects.create(
        performed_exercise=performed_exercise, set_number=(highest or 0) + 1, **fields
    )


def is_performed_exercise_complete(performed_exercise):
    """Whether a performed exercise has nothing obviously left to log -
    the definition training mode's "what's next" stepper (see
    `first_incomplete_performed_exercise`) is built on. A prescribed
    exercise (`set_count` snapshotted from the plan) is complete once it
    has that many sets; a freeform/ad-hoc addition has no target to
    compare against, so it's treated as complete once it has at least one
    set - there's no way to know a user wants a second set on an
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
    sets left to log - training mode's default "current exercise", per
    `is_performed_exercise_complete`. `None` once every exercise in the
    list is done."""
    for performed_exercise in performed_exercises:
        if not is_performed_exercise_complete(performed_exercise):
            return performed_exercise
    return None


def schedule_rest_timer_notification(user, *, seconds, title, body, url=""):
    """Replaces (update_or_create, not create) whatever server-side
    "time's up" notification is already pending for `user` - see
    RestTimerNotification's own docstring for why there's only ever
    one row per user. Called from apps.workouts.views whenever the
    client (static/js/rest-timer.js) starts or adjusts its own
    countdown, so this row's `fire_at` always tracks the client's own
    idea of when the rest period actually ends."""
    RestTimerNotification.objects.update_or_create(
        user=user,
        defaults={
            "fire_at": timezone.now() + timedelta(seconds=seconds),
            "title": title,
            "body": body,
            "url": url,
        },
    )


def cancel_rest_timer_notification(user):
    """Called whenever the client itself ends the countdown on its
    own - naturally finishing (it already showed its own beep/visible
    countdown/notify(), so a second, server-sent one would just be a
    redundant duplicate) or "Skip rest". Harmless no-op if there was
    never a pending row, or the dispatcher already sent and deleted
    one - apps.workouts.tests covers both."""
    RestTimerNotification.objects.filter(user=user).delete()


def due_rest_timer_notifications():
    """apps.core.management.commands.rest_timer_dispatcher's own query
    - every row whose scheduled time has already passed, oldest first
    so a dispatcher that's fallen behind still sends in the order
    users actually started resting."""
    return RestTimerNotification.objects.filter(fire_at__lte=timezone.now()).order_by("fire_at")


def session_summary(session):
    """Set count and total volume (canonical kg) for one session, computed
    from the already-prefetched performed_exercises/sets so the detail page
    can show a summary without another query or any template arithmetic."""
    set_count = 0
    volume = Decimal("0")
    for performed in session.performed_exercises.all():
        for exercise_set in performed.sets.all():
            set_count += 1
            volume += exercise_set.weight * exercise_set.reps
    return {"set_count": set_count, "volume": volume}
