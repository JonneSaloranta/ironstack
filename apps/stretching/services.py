"""Stretching domain logic, kept out of views/templates per CLAUDE.md.

Like apps.workouts.services, this is where snapshot-on-start happens:
starting a session copies each routine item's name/hold/sets/rest onto
the session's own PerformedStretch rows, so editing or retiring a
routine or stretch later never rewrites what a past session says
happened.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from apps.core.charts import build_bar_series
from apps.exercises.models import MuscleGroup

from .models import (
    PerformedStretch,
    PerformedStretchStatus,
    RoutineItem,
    Stretch,
    StretchKind,
    StretchRoutine,
    StretchSession,
    StretchSessionStatus,
)

AD_HOC_REST_SECONDS = 10
MAX_SUGGESTED_STRETCHES = 6
# A system routine has to cover at least this share of the workout's
# muscle groups to be suggested as-is; below that, a tailored list built
# straight from the library serves the user better.
MIN_ROUTINE_COVERAGE = Decimal("0.5")


class SessionAlreadyInProgress(Exception):
    def __init__(self, session):
        super().__init__("A stretching session is already in progress.")
        self.session = session


# --- Visibility -----------------------------------------------------------


def visible_stretches(user, *, include_inactive=False):
    """Stretches a user may use: system ones + their own."""
    qs = Stretch.objects.filter(Q(owner__isnull=True) | Q(owner=user))
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs


def visible_routines(user, *, include_inactive=False):
    qs = StretchRoutine.objects.filter(Q(owner__isnull=True) | Q(owner=user))
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs


def sessions_for(user):
    return StretchSession.objects.filter(user=user)


def active_session(user):
    return sessions_for(user).filter(status=StretchSessionStatus.IN_PROGRESS).first()


# --- Routines -------------------------------------------------------------


def hold_count(sets, per_side):
    return sets * (2 if per_side else 1)


def item_seconds(*, hold_seconds, sets, per_side, rest_seconds):
    """Planned time for one stretch: every hold, plus rest between
    consecutive holds (not after the last one)."""
    holds = hold_count(sets, per_side)
    return holds * hold_seconds + (holds - 1) * rest_seconds


def routine_seconds(routine):
    return sum(
        item_seconds(
            hold_seconds=item.hold_seconds,
            sets=item.sets,
            per_side=item.stretch.per_side,
            rest_seconds=item.rest_seconds,
        )
        for item in routine.items.select_related("stretch")
    )


def add_routine_item(routine, stretch, *, hold_seconds=None, sets=1, rest_seconds=10):
    highest = routine.items.aggregate(highest=Max("order"))["highest"]
    next_order = 0 if highest is None else highest + 1
    return RoutineItem.objects.create(
        routine=routine,
        stretch=stretch,
        order=next_order,
        hold_seconds=hold_seconds or stretch.default_hold_seconds,
        sets=sets,
        rest_seconds=rest_seconds,
    )


@transaction.atomic
def move_routine_item(item, direction):
    """Swap `item` with its neighbour (`direction` -1 = up, +1 = down)."""
    siblings = list(item.routine.items.all())
    index = next(i for i, sibling in enumerate(siblings) if sibling.pk == item.pk)
    target = index + direction
    if not 0 <= target < len(siblings):
        return
    siblings[index], siblings[target] = siblings[target], siblings[index]
    for order, sibling in enumerate(siblings):
        if sibling.order != order:
            sibling.order = order
            sibling.save(update_fields=["order"])


@transaction.atomic
def copy_routine(routine, user):
    """A user's own editable copy of any visible routine — how a built-in
    routine gets customised without touching the shared original."""
    # A system routine's canonical-English text is translated into the
    # user's language on the way into their own copy, which they then
    # own and edit as plain text.
    source_name = routine.name if routine.is_custom else _(routine.name)
    description = routine.description
    if description and not routine.is_custom:
        description = _(description)
    base_name = _("%(name)s (copy)") % {"name": source_name}
    name, suffix = base_name, 2
    while StretchRoutine.objects.filter(owner=user, name=name).exists():
        name = f"{base_name} {suffix}"
        suffix += 1
    copy = StretchRoutine.objects.create(owner=user, name=name, description=description)
    RoutineItem.objects.bulk_create(
        RoutineItem(
            routine=copy,
            stretch=item.stretch,
            order=item.order,
            hold_seconds=item.hold_seconds,
            sets=item.sets,
            rest_seconds=item.rest_seconds,
        )
        for item in routine.items.all()
    )
    return copy


# --- Sessions -------------------------------------------------------------


@transaction.atomic
def start_session(user, *, routine=None, stretches=None, name="", after_workout=None):
    """Start a guided session from `routine`, or from a plain list of
    `stretches` (a post-workout suggestion) at their default holds.

    Only one session may be in progress at a time — raises
    SessionAlreadyInProgress with the existing one so the caller can
    send the user back to it instead.
    """
    existing = active_session(user)
    if existing is not None:
        raise SessionAlreadyInProgress(existing)

    session = StretchSession.objects.create(
        user=user,
        routine=routine,
        after_workout=after_workout,
        name=name or (routine.name if routine else ""),
        status=StretchSessionStatus.IN_PROGRESS,
        started_at=timezone.now(),
        date=timezone.localdate(),
    )
    if routine is not None:
        performed = [
            PerformedStretch(
                session=session,
                stretch=item.stretch,
                order=order,
                stretch_name=item.stretch.name,
                per_side=item.stretch.per_side,
                target_hold_seconds=item.hold_seconds,
                sets=item.sets,
                rest_seconds=item.rest_seconds,
            )
            for order, item in enumerate(routine.items.select_related("stretch"))
        ]
    else:
        performed = [
            PerformedStretch(
                session=session,
                stretch=stretch,
                order=order,
                stretch_name=stretch.name,
                per_side=stretch.per_side,
                target_hold_seconds=stretch.default_hold_seconds,
                sets=1,
                rest_seconds=AD_HOC_REST_SECONDS,
            )
            for order, stretch in enumerate(stretches or [])
        ]
    PerformedStretch.objects.bulk_create(performed)
    return session


def mark_performed(performed, *, done, actual_seconds=None):
    """Record one stretch as done (optionally with the seconds actually
    held) or skipped. Always the user's call — the timer only proposes."""
    performed.status = PerformedStretchStatus.DONE if done else PerformedStretchStatus.SKIPPED
    performed.actual_seconds = actual_seconds if done else None
    performed.save(update_fields=["status", "actual_seconds"])
    return performed


def complete_session(session):
    now = timezone.now()
    session.status = StretchSessionStatus.COMPLETED
    session.completed_at = now
    session.duration = max(now - session.started_at, timedelta())
    session.save(update_fields=["status", "completed_at", "duration", "updated_at"])
    return session


def abandon_session(session):
    session.status = StretchSessionStatus.ABANDONED
    session.completed_at = timezone.now()
    session.save(update_fields=["status", "completed_at", "updated_at"])
    return session


def quick_log(user, *, date, duration, routine=None, name="", notes=""):
    """Log a session after the fact, with just a duration — no guided
    timer, no per-stretch detail."""
    now = timezone.now()
    return StretchSession.objects.create(
        user=user,
        routine=routine,
        name=name or (routine.name if routine else ""),
        status=StretchSessionStatus.COMPLETED,
        date=date,
        started_at=now,
        completed_at=now,
        duration=duration,
        notes=notes,
    )


def timer_steps(session):
    """The guided timer's playlist for a session's still-pending
    stretches: one "hold" step per set × side, with a "rest" step between
    consecutive holds wherever the stretch has rest configured. Built
    here (not in the JS) so the sequencing rules are tested in Python;
    static/js/stretch-timer.js just plays it."""
    steps = []
    pending = list(session.performed_stretches.filter(status=PerformedStretchStatus.PENDING))
    for performed in pending:
        sides = ["left", "right"] if performed.per_side else [""]
        holds = [
            (set_number, side)
            for set_number in range(1, performed.sets + 1)
            for side in sides
        ]
        for index, (set_number, side) in enumerate(holds):
            is_last_hold = index == len(holds) - 1
            steps.append(
                {
                    "kind": "hold",
                    "performed": performed.pk,
                    "seconds": performed.target_hold_seconds,
                    "set": set_number,
                    "sets": performed.sets,
                    "side": side,
                    "last_of_stretch": is_last_hold,
                }
            )
            if not is_last_hold and performed.rest_seconds:
                steps.append(
                    {
                        "kind": "rest",
                        "performed": performed.pk,
                        "seconds": performed.rest_seconds,
                        "set": set_number,
                        "sets": performed.sets,
                        "side": side,
                        "last_of_stretch": False,
                    }
                )
    return steps


# --- Post-workout suggestion ---------------------------------------------


@dataclass(frozen=True)
class CooldownSuggestion:
    muscle_groups: list
    routine: StretchRoutine | None = None
    stretches: list = field(default_factory=list)


def trained_muscle_groups(workout_session):
    return list(
        MuscleGroup.objects.filter(
            primary_exercises__performedexercise__session=workout_session
        ).distinct()
    )


def suggest_cooldown(workout_session):
    """What to stretch after `workout_session`, based on the primary
    muscle groups of the exercises it contained. Prefers an existing
    routine (system or the user's own) that covers most of those
    muscles; otherwise builds a short list straight from the library.
    Returns None if nothing matches. Only ever a suggestion — nothing is
    started until the user taps it."""
    user = workout_session.user
    muscles = trained_muscle_groups(workout_session)
    if not muscles:
        return None
    muscle_ids = {muscle.pk for muscle in muscles}

    best, best_coverage, best_seconds = None, Decimal("0"), None
    for routine in visible_routines(user).prefetch_related("items__stretch__muscle_groups"):
        # Only static stretches count: a dynamic warm-up routine that
        # happens to touch the same muscles isn't a cool-down.
        covered = {
            group.pk
            for item in routine.items.all()
            if item.stretch.kind == StretchKind.STATIC
            for group in item.stretch.muscle_groups.all()
        } & muscle_ids
        coverage = Decimal(len(covered)) / Decimal(len(muscle_ids))
        seconds = routine_seconds(routine)
        # Ties go to the shorter routine — a cool-down should be quick.
        if coverage > best_coverage or (
            coverage == best_coverage and best is not None and seconds < best_seconds
        ):
            best, best_coverage, best_seconds = routine, coverage, seconds
    if best is not None and best_coverage >= MIN_ROUTINE_COVERAGE:
        return CooldownSuggestion(muscle_groups=muscles, routine=best)

    stretches = []
    candidates = (
        visible_stretches(user)
        .filter(kind=StretchKind.STATIC)
        .prefetch_related("muscle_groups")
        # System stretches first, then the user's own.
        .order_by("owner_id", "name")
    )
    for muscle in muscles:
        for stretch in candidates:
            if stretch in stretches:
                continue
            if any(group.pk == muscle.pk for group in stretch.muscle_groups.all()):
                stretches.append(stretch)
                break
        if len(stretches) >= MAX_SUGGESTED_STRETCHES:
            break
    if not stretches:
        return None
    return CooldownSuggestion(muscle_groups=muscles, stretches=stretches)


# --- Stats ----------------------------------------------------------------


@dataclass(frozen=True)
class StretchSummary:
    count: int
    total_duration: timedelta
    last_7_days_count: int
    last_7_days_duration: timedelta
    streak_days: int


def completed_sessions(user):
    return sessions_for(user).filter(status=StretchSessionStatus.COMPLETED)


def current_streak(dates, today):
    """Consecutive days with a session, ending today — or yesterday, so
    the streak doesn't read as broken before today's stretch is done."""
    dates = set(dates)
    day = today if today in dates else today - timedelta(days=1)
    streak = 0
    while day in dates:
        streak += 1
        day -= timedelta(days=1)
    return streak


def summarize(user, *, today=None):
    today = today or timezone.localdate()
    sessions = list(completed_sessions(user).only("date", "duration"))
    week_start = today - timedelta(days=6)
    recent = [s for s in sessions if week_start <= s.date <= today]
    return StretchSummary(
        count=len(sessions),
        total_duration=sum((s.duration or timedelta() for s in sessions), timedelta()),
        last_7_days_count=len(recent),
        last_7_days_duration=sum((s.duration or timedelta() for s in recent), timedelta()),
        streak_days=current_streak((s.date for s in sessions), today),
    )


def weekly_minutes(user, *, weeks=8, today=None):
    """(week start date, minutes) for the last `weeks` ISO weeks, oldest
    first, including weeks with nothing logged."""
    today = today or timezone.localdate()
    this_monday = today - timedelta(days=today.weekday())
    first_monday = this_monday - timedelta(weeks=weeks - 1)
    totals = {first_monday + timedelta(weeks=i): timedelta() for i in range(weeks)}
    for session in completed_sessions(user).filter(date__gte=first_monday).only(
        "date", "duration"
    ):
        monday = session.date - timedelta(days=session.date.weekday())
        totals[monday] += session.duration or timedelta()
    return [
        (monday, (Decimal(total.total_seconds()) / Decimal(60)).quantize(Decimal("1")))
        for monday, total in totals.items()
    ]


def weekly_chart(user, *, weeks=8, today=None):
    rows = weekly_minutes(user, weeks=weeks, today=today)
    return build_bar_series(
        (date_format(monday, format="SHORT_DATE_FORMAT", use_l10n=True), minutes)
        for monday, minutes in rows
    )


def calendar_minutes(user, year, month):
    """{date: total minutes} of completed stretching in one month — the
    dashboard calendar's stretching marker (apps.core.views)."""
    totals = {}
    for session in completed_sessions(user).filter(
        date__year=year, date__month=month
    ).only("date", "duration"):
        totals[session.date] = totals.get(session.date, timedelta()) + (
            session.duration or timedelta()
        )
    return {day: round(total.total_seconds() / 60) for day, total in totals.items()}
