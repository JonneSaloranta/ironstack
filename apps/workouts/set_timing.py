"""How long a set takes - the "set cycle" (docs/ANALYTICS.md "Set cycle time").

Only a set's *completion* is recorded (`ExerciseSet.performed_at`), so a
set's own duration can't be measured. What can be measured is the gap
between two consecutive sets of one exercise: rest, changing the weights
and the next set itself - the set cycle. That is also what a workout's
length is made of. The first set of an exercise is reached through a
"transition" (finishing the previous exercise, setting up the next),
measured the same way across exercises.

Estimates come from every user's completed workouts on this instance:
the median and quartiles of each group (exercise x rep range), after
dropping what can't be a real set cycle:

1. hard limits - under 10 s (a double tap, sets typed in afterwards) or
   over 20 min (forgot to log, left the workout open), sets logged
   within 5 s of each other, sets whose time was edited or supplied
   later (performed_at far from created_at), a set of another exercise
   in between (supersets/circuits), warm-up sets;
2. Tukey's fences on the log of the gaps in each group - gaps are skewed
   (long rests are common, very short ones aren't), so on a log scale
   the fences cut misfires and forgotten timers without trimming normal
   long rests.

Each user contributes at most their latest PER_USER_CAP gaps to a group,
so one very active lifter doesn't decide everyone's median (and on a
single-user instance the estimate follows the recent pace). A group is
used once it has MIN_SAMPLES gaps, however many users they come from -
a self-hosted instance often has only one.
"""

import math
import statistics
from dataclasses import dataclass
from datetime import timedelta

from django.core.cache import cache
from django.db.models import F, Window
from django.db.models.functions import Lag
from django.utils import timezone

from .models import ExerciseSet, WorkoutSessionStatus

MIN_GAP_SECONDS = 10
MAX_GAP_SECONDS = 20 * 60
MIN_CREATED_GAP_SECONDS = 5
# performed_at further than this from created_at was typed in or edited,
# not logged at the time.
MAX_LOGGING_DELAY_SECONDS = 60
PER_USER_CAP = 50
MIN_SAMPLES = 20
MIN_OWN_SAMPLES = 5
TUKEY_K = 1.5
# The fences never get narrower than this on the log scale (about -26 %
# / +35 % around the quartiles): with many identical gaps the IQR is 0
# and the fences would otherwise drop every gap that differs at all.
MIN_LOG_IQR = 0.2
HISTORY_DAYS = 730
CACHE_KEY = "set-timing:instance-stats:v1"
CACHE_SECONDS = 6 * 60 * 60

# (low, high) inclusive; reps at or above the last low land in it.
REP_BUCKETS = ((1, 3), (4, 6), (7, 10), (11, 15), (16, None))

# Used only where the instance has too little data: about 30 s to set up
# and finish, 3 s a rep, 90 s rest. Quartiles a rough -25 % / +30 %.
DEFAULT_CYCLE_BASE_SECONDS = 120
DEFAULT_SECONDS_PER_REP = 3
DEFAULT_TRANSITION_SECONDS = 180
DEFAULT_LOW_FACTOR = 0.75
DEFAULT_HIGH_FACTOR = 1.3
DEFAULT_REPS = 10

# Where an estimate came from, most to least specific.
SOURCE_EXERCISE_REPS = "exercise_reps"
SOURCE_EXERCISE = "exercise"
SOURCE_MOVEMENT_REPS = "movement_reps"
SOURCE_ALL_REPS = "all_reps"
SOURCE_TRANSITION = "transition"
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class GapStats:
    """Median and quartiles (seconds) of one group's kept gaps."""

    median: float
    q1: float
    q3: float
    sample_size: int
    user_count: int

    @property
    def spread(self):
        """The "±" shown with the median: half the interquartile range,
        so median ± spread covers the middle half of the sets."""
        return (self.q3 - self.q1) / 2


@dataclass(frozen=True)
class CycleEstimate:
    stats: GapStats
    source: str

    @property
    def is_default(self):
        return self.source == SOURCE_DEFAULT


@dataclass(frozen=True)
class DurationEstimate:
    """A whole workout (or what's left of one): `low`/`high` are the sums
    of each set's quartiles. Summed directly rather than as variances: one
    person tends to be slow (or fast) on every set, so the uncertainty
    doesn't cancel out."""

    low: float
    high: float
    rough: bool

    @property
    def middle(self):
        return (self.low + self.high) / 2

    @property
    def spread(self):
        return (self.high - self.low) / 2


def rep_bucket(reps):
    """Index into REP_BUCKETS for a rep count."""
    reps = max(int(reps or 1), 1)
    for index, (_low, high) in enumerate(REP_BUCKETS):
        if high is None or reps <= high:
            return index
    return len(REP_BUCKETS) - 1


def rep_bucket_label(index):
    low, high = REP_BUCKETS[index]
    return f"{low}+" if high is None else f"{low}-{high}"


def _seconds(delta):
    return delta.total_seconds() if delta is not None else None


def _logged_live(performed_at, created_at):
    return abs(_seconds(performed_at - created_at)) <= MAX_LOGGING_DELAY_SECONDS


def _gap_or_none(at, created, prev_at, prev_created):
    """The gap in seconds between two sets, or None when it fails a hard
    limit (see the module docstring, step 1)."""
    if prev_at is None:
        return None
    if not (_logged_live(at, created) and _logged_live(prev_at, prev_created)):
        return None
    if _seconds(created - prev_created) < MIN_CREATED_GAP_SECONDS:
        return None
    gap = _seconds(at - prev_at)
    if not MIN_GAP_SECONDS <= gap <= MAX_GAP_SECONDS:
        return None
    return gap


def _gaps(user=None):
    """(cycles, transitions) from completed sessions. A cycle is a dict
    with exercise, movement type, user, reps, time and seconds; a
    transition the same without exercise/reps."""
    by_exercise = {
        "partition_by": [F("performed_exercise_id")],
        "order_by": [F("set_number").asc()],
    }
    by_session = {
        "partition_by": [F("performed_exercise__session_id")],
        "order_by": [F("performed_at").asc(), F("pk").asc()],
    }
    rows = (
        ExerciseSet.objects.filter(
            performed_exercise__session__status=WorkoutSessionStatus.COMPLETED,
            performed_at__gte=timezone.now() - timedelta(days=HISTORY_DAYS),
        )
        .annotate(
            prev_pk=Window(Lag("pk"), **by_exercise),
            prev_at=Window(Lag("performed_at"), **by_exercise),
            prev_created=Window(Lag("created_at"), **by_exercise),
            prev_warmup=Window(Lag("is_warmup"), **by_exercise),
            session_prev_pk=Window(Lag("pk"), **by_session),
            session_prev_at=Window(Lag("performed_at"), **by_session),
            session_prev_created=Window(Lag("created_at"), **by_session),
            session_prev_warmup=Window(Lag("is_warmup"), **by_session),
        )
        .values(
            "reps",
            "is_warmup",
            "performed_at",
            "created_at",
            "prev_pk",
            "prev_at",
            "prev_created",
            "prev_warmup",
            "session_prev_pk",
            "session_prev_at",
            "session_prev_created",
            "session_prev_warmup",
            exercise=F("performed_exercise__exercise_id"),
            movement=F("performed_exercise__exercise__movement_type"),
            user=F("performed_exercise__session__user_id"),
        )
    )
    if user is not None:
        rows = rows.filter(performed_exercise__session__user=user)

    cycles, transitions = [], []
    for row in rows:
        if row["is_warmup"]:
            continue
        if row["prev_pk"] is not None:
            # Another set logged in between = a superset or circuit.
            if row["prev_warmup"] or row["session_prev_pk"] != row["prev_pk"]:
                continue
            gap = _gap_or_none(
                row["performed_at"], row["created_at"], row["prev_at"], row["prev_created"]
            )
            if gap is not None:
                cycles.append({**row, "seconds": gap})
        elif row["session_prev_pk"] is not None and not row["session_prev_warmup"]:
            gap = _gap_or_none(
                row["performed_at"],
                row["created_at"],
                row["session_prev_at"],
                row["session_prev_created"],
            )
            if gap is not None:
                transitions.append({**row, "seconds": gap})
    return cycles, transitions


def _quartiles(values):
    if len(values) == 1:
        return values[0], values[0], values[0]
    q1, median, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return q1, median, q3


def _without_outliers(values):
    """Tukey's fences on log(seconds) (module docstring, step 2)."""
    if len(values) < 4:
        return values
    logs = [math.log(v) for v in values]
    q1, _median, q3 = _quartiles(logs)
    iqr = max(q3 - q1, MIN_LOG_IQR)
    low, high = q1 - TUKEY_K * iqr, q3 + TUKEY_K * iqr
    return [v for v, lg in zip(values, logs, strict=True) if low <= lg <= high]


def _capped(samples):
    """At most PER_USER_CAP of each user's latest samples."""
    per_user = {}
    for sample in sorted(samples, key=lambda s: s["performed_at"], reverse=True):
        kept = per_user.setdefault(sample["user"], [])
        if len(kept) < PER_USER_CAP:
            kept.append(sample)
    return [s for kept in per_user.values() for s in kept]


def summarize(samples):
    """GapStats for one group of samples, or None if nothing is left."""
    samples = _capped(samples)
    kept = _without_outliers([s["seconds"] for s in samples])
    if not kept:
        return None
    kept_set = set(kept)
    users = {s["user"] for s in samples if s["seconds"] in kept_set}
    q1, median, q3 = _quartiles(sorted(kept))
    return GapStats(median=median, q1=q1, q3=q3, sample_size=len(kept), user_count=len(users))


def _grouped(samples, key):
    groups = {}
    for sample in samples:
        groups.setdefault(key(sample), []).append(sample)
    return groups


def compute_instance_stats():
    """Every group's stats, keyed (SOURCE_..., group value[, rep bucket])."""
    cycles, transitions = _gaps()
    groupings = {
        SOURCE_EXERCISE_REPS: lambda s: (s["exercise"], rep_bucket(s["reps"])),
        SOURCE_EXERCISE: lambda s: s["exercise"],
        SOURCE_MOVEMENT_REPS: lambda s: (s["movement"], rep_bucket(s["reps"])),
        SOURCE_ALL_REPS: lambda s: rep_bucket(s["reps"]),
    }
    stats = {}
    for source, key in groupings.items():
        for group, samples in _grouped(cycles, key).items():
            summary = summarize(samples)
            if summary is not None:
                stats[(source, group)] = summary
    transition = summarize(transitions)
    if transition is not None:
        stats[(SOURCE_TRANSITION, None)] = transition
    return stats


def instance_stats():
    """compute_instance_stats(), cached - the instance's pace changes slowly."""
    stats = cache.get(CACHE_KEY)
    if stats is None:
        stats = compute_instance_stats()
        cache.set(CACHE_KEY, stats, CACHE_SECONDS)
    return stats


def clear_cache():
    cache.delete(CACHE_KEY)


def _usable(stats):
    return stats is not None and stats.sample_size >= MIN_SAMPLES


def _default_cycle(reps):
    median = DEFAULT_CYCLE_BASE_SECONDS + DEFAULT_SECONDS_PER_REP * reps
    return GapStats(
        median=median,
        q1=median * DEFAULT_LOW_FACTOR,
        q3=median * DEFAULT_HIGH_FACTOR,
        sample_size=0,
        user_count=0,
    )


def estimate_cycle(exercise, reps, stats=None):
    """The set cycle for `reps` reps of `exercise`, from the most specific
    group with enough data: this exercise at this rep range, this
    exercise, its movement type at this rep range, every exercise at
    this rep range - then the default formula."""
    stats = instance_stats() if stats is None else stats
    reps = reps or DEFAULT_REPS
    bucket = rep_bucket(reps)
    for source, group in (
        (SOURCE_EXERCISE_REPS, (exercise.pk, bucket)),
        (SOURCE_EXERCISE, exercise.pk),
        (SOURCE_MOVEMENT_REPS, (exercise.movement_type, bucket)),
        (SOURCE_ALL_REPS, bucket),
    ):
        found = stats.get((source, group))
        if _usable(found):
            return CycleEstimate(stats=found, source=source)
    return CycleEstimate(stats=_default_cycle(reps), source=SOURCE_DEFAULT)


def estimate_transition(stats=None):
    stats = instance_stats() if stats is None else stats
    found = stats.get((SOURCE_TRANSITION, None))
    if _usable(found):
        return CycleEstimate(stats=found, source=SOURCE_TRANSITION)
    seconds = DEFAULT_TRANSITION_SECONDS
    return CycleEstimate(
        stats=GapStats(
            median=seconds,
            q1=seconds * DEFAULT_LOW_FACTOR,
            q3=seconds * DEFAULT_HIGH_FACTOR,
            sample_size=0,
            user_count=0,
        ),
        source=SOURCE_DEFAULT,
    )


def _typical_reps(min_reps, max_reps):
    if min_reps and max_reps:
        return round((min_reps + max_reps) / 2)
    return min_reps or max_reps or DEFAULT_REPS


def _sum(parts):
    """parts: (count, CycleEstimate) pairs -> DurationEstimate or None."""
    parts = [(count, estimate) for count, estimate in parts if count > 0]
    if not parts:
        return None
    return DurationEstimate(
        low=sum(count * estimate.stats.q1 for count, estimate in parts),
        high=sum(count * estimate.stats.q3 for count, estimate in parts),
        rough=any(estimate.is_default for _count, estimate in parts),
    )


def _exercise_parts(exercise, set_count, min_reps, max_reps, stats):
    """One transition into the exercise (its first set) plus a set cycle
    for every further set."""
    if not set_count:
        return []
    cycle = estimate_cycle(exercise, _typical_reps(min_reps, max_reps), stats)
    return [(1, estimate_transition(stats)), (set_count - 1, cycle)]


def estimate_exercise(exercise, set_count, min_reps, max_reps, stats=None):
    """(DurationEstimate or None, CycleEstimate) for one prescribed
    exercise - what it adds to a workout, and one set of it. Used live on
    the add/edit-exercise form (apps.programs.views.prescription_estimate)."""
    stats = instance_stats() if stats is None else stats
    cycle = estimate_cycle(exercise, _typical_reps(min_reps, max_reps), stats)
    return _sum(_exercise_parts(exercise, set_count, min_reps, max_reps, stats)), cycle


def estimate_workout(workout, stats=None, replace=None, extra=None):
    """How long a program's workout takes: each exercise's transition and
    set cycles summed. Reads `workout.prescriptions.all()`, so prefetch it.

    `replace` (a prescription pk) leaves that prescription out and `extra`
    (exercise, set_count, min_reps, max_reps) adds one - the workout as it
    would be with the add/edit-exercise form's current values."""
    stats = instance_stats() if stats is None else stats
    parts = []
    for prescription in workout.prescriptions.all():
        if replace is not None and prescription.pk == replace:
            continue
        parts += _exercise_parts(
            prescription.exercise,
            prescription.set_count,
            prescription.min_reps,
            prescription.max_reps,
            stats,
        )
    if extra is not None:
        parts += _exercise_parts(*extra, stats)
    return _sum(parts)


def estimate_remaining(performed_exercises, stats=None):
    """What's left of a workout in progress. A planned exercise has its
    snapshotted set count left minus what's logged; an unplanned one
    with nothing logged yet counts as one set (training mode's own
    "complete" rule, services.is_performed_exercise_complete)."""
    stats = instance_stats() if stats is None else stats
    transition = estimate_transition(stats)
    parts = []
    for performed in performed_exercises:
        sets = list(performed.sets.all())
        if performed.set_count:
            remaining = max(performed.set_count - len(sets), 0)
        else:
            remaining = 0 if sets else 1
        if not remaining:
            continue
        reps = _typical_reps(performed.min_reps, performed.max_reps)
        if not (performed.min_reps or performed.max_reps) and sets:
            reps = sets[-1].reps
        cycle = estimate_cycle(performed.exercise, reps, stats)
        if sets:
            parts.append((remaining, cycle))
        else:
            parts.append((1, transition))
            parts.append((remaining - 1, cycle))
    return _sum(parts)


def own_cycle(user, exercise):
    """The user's own set cycle on `exercise` (all rep ranges), or None
    with fewer than MIN_OWN_SAMPLES gaps."""
    cycles, _transitions = _gaps(user=user)
    summary = summarize([c for c in cycles if c["exercise"] == exercise.pk])
    if summary is None or summary.sample_size < MIN_OWN_SAMPLES:
        return None
    return summary


def instance_cycle(exercise, stats=None):
    """Everyone's set cycle on `exercise` (all rep ranges), or None when
    the instance doesn't have enough of it."""
    stats = instance_stats() if stats is None else stats
    found = stats.get((SOURCE_EXERCISE, exercise.pk))
    return found if _usable(found) else None
