"""Progression engine — decides how a prescribed exercise's next weight
should change, per docs/PROGRESSION.md.

Pure domain logic: reads workout/PR history via the ORM but is otherwise
independent of HTTP/templates, deterministic for the same inputs, and
explainable (every result carries a `reason`) — see docs/PROGRESSION.md
"Requirements". Turning this into an actual suggestion *UI* (confidence
scoring, letting the user override, RPE/RIR data-entry affordances) is
Phase 7 (docs/ROADMAP.md "Smart suggestions"); this phase only has to make
the underlying decision correctly.

Nothing here is persisted — a progression decision is recomputed live each
time from `ExerciseSet`/`PersonalRecord` history, the same "derive, don't
cache" approach apps.records uses for PRs. That's also why this app has no
models: there is no new state to store.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from apps.programs.models import ProgressionMethod
from apps.records.models import PersonalRecord, PRType
from apps.records.one_rep_max import OneRepMaxCalculator
from apps.workouts.models import PerformedExercise, WorkoutSessionStatus

# A session "fails" a weight if it doesn't hit the rep target or has a
# failed set. This many consecutive failures at the same weight escalates
# a plain "maintain" into a deload recommendation — see docs/PROGRESSION.md
# "Failure handling": repeated failure, not a single bad session.
FAILURE_STREAK_FOR_DELOAD = 2

# A patient variant of "hit the top of the range" (see REP_RANGE below)
# waits for this many consecutive sessions at the top before increasing,
# instead of double progression's single-session trigger.
TOP_OF_RANGE_STREAK_FOR_INCREASE = 2

DELOAD_FACTOR = Decimal("0.9")
TWO_PLACES = Decimal("0.01")

_one_rep_max = OneRepMaxCalculator()


class ProgressionAction(str, Enum):
    INCREASE = "increase"
    MAINTAIN = "maintain"
    DECREASE = "decrease"
    DELOAD = "deload"
    # Percentage-based isn't a trend judgement — it's a direct formula
    # result off the current 1RM source, so it gets its own action rather
    # than being forced into increase/maintain/decrease.
    CALCULATED = "calculated"
    MANUAL = "manual"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class ProgressionResult:
    action: ProgressionAction
    suggested_weight: Decimal | None
    reason: str
    sessions_considered: int
    one_rm_source: str | None = None


@dataclass(frozen=True)
class _Attempt:
    """One past completed session's working (non-warmup) sets for an
    exercise, summarized for progression decisions."""

    sets: tuple
    weight: Decimal  # heaviest working-set weight used this attempt
    any_failed: bool
    all_met_min_reps: bool
    all_met_max_reps: bool
    target_rir: int | None  # snapshotted at the time, not the live prescription


def calculate_progression(user, prescription, *, manual_one_rm=None) -> ProgressionResult:
    """Decide the next target weight for `prescription.exercise`, based on
    `user`'s history and the method configured on `prescription`.

    `manual_one_rm`: an optional caller-supplied 1RM (e.g. typed in by the
    user) — only consulted by percentage-based progression, as the
    highest-priority of the three 1RM sources docs/PROGRESSION.md
    requires. There is no persisted "manual 1RM" field; a caller (a
    future UI) can offer one at call time without this engine needing to
    store it.
    """
    handler = _HANDLERS.get(prescription.progression_method, _manual)
    if handler is _percentage_based:
        return handler(user, prescription, manual_one_rm)
    return handler(user, prescription)


def _recent_attempts(user, exercise, limit=5):
    performed_exercises = (
        PerformedExercise.objects.filter(
            exercise=exercise,
            session__user=user,
            session__status=WorkoutSessionStatus.COMPLETED,
        )
        .prefetch_related("sets")
        .order_by("-session__started_at", "-id")[:20]
    )
    attempts = []
    for performed_exercise in performed_exercises:
        attempt = _summarize(performed_exercise)
        if attempt is not None:
            attempts.append(attempt)
        if len(attempts) >= limit:
            break
    return attempts


def _summarize(performed_exercise):
    working_sets = tuple(s for s in performed_exercise.sets.all() if not s.is_warmup)
    if not working_sets:
        return None
    weight = max(s.weight for s in working_sets)
    min_reps = performed_exercise.min_reps
    max_reps = performed_exercise.max_reps
    return _Attempt(
        sets=working_sets,
        weight=weight,
        any_failed=any(s.is_failure for s in working_sets),
        all_met_min_reps=all(s.reps >= min_reps for s in working_sets) if min_reps else True,
        all_met_max_reps=all(s.reps >= max_reps for s in working_sets) if max_reps else False,
        target_rir=performed_exercise.target_rir,
    )


def _consecutive_failures_at_weight(attempts, weight):
    """How many of the most recent attempts, walking backward, were both
    at `weight` and unsuccessful (failed set or missed the rep floor)."""
    count = 0
    for attempt in attempts:
        if attempt.weight != weight:
            break
        if attempt.any_failed or not attempt.all_met_min_reps:
            count += 1
        else:
            break
    return count


def _consecutive_top_of_range_at_weight(attempts, weight):
    count = 0
    for attempt in attempts:
        if attempt.weight != weight:
            break
        if attempt.all_met_max_reps and not attempt.any_failed:
            count += 1
        else:
            break
    return count


def _no_history_result(prescription):
    return ProgressionResult(
        ProgressionAction.INSUFFICIENT_DATA,
        prescription.target_weight,
        "No completed history for this exercise yet — starting at the prescribed target.",
        0,
    )


def _manual(user, prescription):
    return ProgressionResult(
        ProgressionAction.MANUAL,
        prescription.target_weight,
        "Manual progression — you control the next weight and target.",
        0,
    )


def _maintenance(user, prescription):
    attempts = _recent_attempts(user, prescription.exercise, limit=FAILURE_STREAK_FOR_DELOAD + 1)
    if not attempts:
        return _no_history_result(prescription)

    latest = attempts[0]
    failure_streak = _consecutive_failures_at_weight(attempts, latest.weight)
    if failure_streak >= FAILURE_STREAK_FOR_DELOAD:
        deload_weight = (latest.weight * DELOAD_FACTOR).quantize(TWO_PLACES)
        return ProgressionResult(
            ProgressionAction.DELOAD,
            deload_weight,
            f"Missed the target {failure_streak} sessions in a row at {latest.weight} kg — "
            f"even maintenance loading should come down, to {deload_weight} kg.",
            len(attempts),
        )

    return ProgressionResult(
        ProgressionAction.MAINTAIN,
        latest.weight,
        "Maintenance — keeping the same load.",
        len(attempts),
    )


def _linear(user, prescription):
    attempts = _recent_attempts(user, prescription.exercise, limit=FAILURE_STREAK_FOR_DELOAD + 1)
    if not attempts:
        return _no_history_result(prescription)

    latest = attempts[0]
    increment = prescription.weight_increment or Decimal("0")

    if latest.all_met_min_reps and not latest.any_failed:
        new_weight = latest.weight + increment
        return ProgressionResult(
            ProgressionAction.INCREASE,
            new_weight,
            f"Hit every rep last session at {latest.weight} kg — adding {increment} kg.",
            len(attempts),
        )

    failure_streak = _consecutive_failures_at_weight(attempts, latest.weight)
    if failure_streak >= FAILURE_STREAK_FOR_DELOAD:
        deload_weight = (latest.weight * DELOAD_FACTOR).quantize(TWO_PLACES)
        return ProgressionResult(
            ProgressionAction.DELOAD,
            deload_weight,
            f"Missed target reps {failure_streak} sessions in a row at {latest.weight} kg — "
            f"recommending a deload to {deload_weight} kg.",
            len(attempts),
        )

    return ProgressionResult(
        ProgressionAction.MAINTAIN,
        latest.weight,
        f"Missed target reps last session at {latest.weight} kg — try the same weight again "
        "before increasing.",
        len(attempts),
    )


def _double_progression(user, prescription):
    attempts = _recent_attempts(user, prescription.exercise, limit=FAILURE_STREAK_FOR_DELOAD + 1)
    if not attempts:
        return _no_history_result(prescription)

    latest = attempts[0]
    increment = prescription.weight_increment or Decimal("0")

    if latest.all_met_max_reps and not latest.any_failed:
        new_weight = latest.weight + increment
        return ProgressionResult(
            ProgressionAction.INCREASE,
            new_weight,
            f"Hit the top of your rep range on every set at {latest.weight} kg — adding "
            f"{increment} kg and resetting to the bottom of the range.",
            len(attempts),
        )

    if latest.all_met_min_reps and not latest.any_failed:
        return ProgressionResult(
            ProgressionAction.MAINTAIN,
            latest.weight,
            f"Still within your rep range at {latest.weight} kg — repeat the same weight and "
            "aim for more reps next time.",
            len(attempts),
        )

    failure_streak = _consecutive_failures_at_weight(attempts, latest.weight)
    if failure_streak >= FAILURE_STREAK_FOR_DELOAD:
        deload_weight = (latest.weight * DELOAD_FACTOR).quantize(TWO_PLACES)
        return ProgressionResult(
            ProgressionAction.DELOAD,
            deload_weight,
            f"Missed the bottom of your rep range {failure_streak} sessions in a row at "
            f"{latest.weight} kg — recommending a deload to {deload_weight} kg.",
            len(attempts),
        )

    return ProgressionResult(
        ProgressionAction.MAINTAIN,
        latest.weight,
        f"Missed the bottom of your rep range last session at {latest.weight} kg — try the "
        "same weight again.",
        len(attempts),
    )


def _rep_range(user, prescription):
    """A more patient sibling of double progression: requires
    `TOP_OF_RANGE_STREAK_FOR_INCREASE` consecutive sessions at the top of
    the range (not just one) before recommending more weight — see
    docs/PROGRESSION.md's "recent performance trend should be considered"
    applied specifically to the upside decision."""
    history_limit = max(TOP_OF_RANGE_STREAK_FOR_INCREASE, FAILURE_STREAK_FOR_DELOAD) + 1
    attempts = _recent_attempts(user, prescription.exercise, limit=history_limit)
    if not attempts:
        return _no_history_result(prescription)

    latest = attempts[0]
    increment = prescription.weight_increment or Decimal("0")
    top_streak = _consecutive_top_of_range_at_weight(attempts, latest.weight)

    if top_streak >= TOP_OF_RANGE_STREAK_FOR_INCREASE:
        new_weight = latest.weight + increment
        return ProgressionResult(
            ProgressionAction.INCREASE,
            new_weight,
            f"Hit the top of your rep range for {top_streak} sessions in a row at "
            f"{latest.weight} kg — adding {increment} kg.",
            len(attempts),
        )

    if latest.all_met_min_reps and not latest.any_failed:
        return ProgressionResult(
            ProgressionAction.MAINTAIN,
            latest.weight,
            f"Within your rep range at {latest.weight} kg — keep working toward the top of "
            "the range before increasing.",
            len(attempts),
        )

    failure_streak = _consecutive_failures_at_weight(attempts, latest.weight)
    if failure_streak >= FAILURE_STREAK_FOR_DELOAD:
        deload_weight = (latest.weight * DELOAD_FACTOR).quantize(TWO_PLACES)
        return ProgressionResult(
            ProgressionAction.DELOAD,
            deload_weight,
            f"Missed the bottom of your rep range {failure_streak} sessions in a row at "
            f"{latest.weight} kg — recommending a deload to {deload_weight} kg.",
            len(attempts),
        )

    return ProgressionResult(
        ProgressionAction.MAINTAIN,
        latest.weight,
        f"Missed the bottom of your rep range last session at {latest.weight} kg — try the "
        "same weight again.",
        len(attempts),
    )


def _resolve_one_rm(user, exercise, manual_one_rm):
    """The three sources docs/PROGRESSION.md requires, in priority order:
    a manually supplied value, else the best-ever recorded estimated-1RM
    PR, else a fresh live estimate off the most recent single set."""
    if manual_one_rm is not None:
        return manual_one_rm, "manual"

    latest_pr = (
        PersonalRecord.objects.filter(
            user=user, exercise=exercise, record_type=PRType.ESTIMATED_1RM
        )
        .order_by("-achieved_at")
        .first()
    )
    if latest_pr is not None:
        return latest_pr.value, "latest_pr"

    attempts = _recent_attempts(user, exercise, limit=1)
    if attempts:
        best_set = max(attempts[0].sets, key=lambda s: (s.weight, s.reps))
        return _one_rep_max.estimate(best_set.weight, best_set.reps), "estimated"

    return None, None


_ONE_RM_SOURCE_LABELS = {
    "manual": "a manually entered 1RM",
    "latest_pr": "your latest estimated-1RM PR",
    "estimated": "a live estimate from your most recent set",
}


def _percentage_based(user, prescription, manual_one_rm=None):
    percentage = prescription.percentage_target
    if not percentage:
        return ProgressionResult(
            ProgressionAction.INSUFFICIENT_DATA,
            prescription.target_weight,
            "No percentage target configured on this prescription.",
            0,
        )

    one_rm, source = _resolve_one_rm(user, prescription.exercise, manual_one_rm)
    if one_rm is None:
        return ProgressionResult(
            ProgressionAction.INSUFFICIENT_DATA,
            None,
            "No 1RM available yet — log a set first, or enter your 1RM manually.",
            0,
        )

    suggested = (one_rm * percentage / Decimal("100")).quantize(TWO_PLACES)
    return ProgressionResult(
        ProgressionAction.CALCULATED,
        suggested,
        f"{percentage}% of {one_rm} kg ({_ONE_RM_SOURCE_LABELS[source]}) = {suggested} kg.",
        0,
        one_rm_source=source,
    )


def _rpe_rir(user, prescription):
    attempts = _recent_attempts(user, prescription.exercise, limit=FAILURE_STREAK_FOR_DELOAD + 1)
    if not attempts:
        return _no_history_result(prescription)

    latest = attempts[0]
    # Judge against what was actually asked of the user *that session*
    # (the snapshot), not whatever the live prescription says now — same
    # historical-trustworthiness reasoning as min_reps/max_reps above.
    target_rir = latest.target_rir
    actual_rir_values = [s.rir for s in latest.sets if s.rir is not None]

    if target_rir is None or not actual_rir_values:
        # Never fabricate RPE/RIR — fall back to reporting no decision
        # rather than guessing, per docs/PROGRESSION.md.
        return ProgressionResult(
            ProgressionAction.INSUFFICIENT_DATA,
            latest.weight,
            "No RIR logged against a target yet — log sets with RIR to use this method.",
            len(attempts),
        )

    actual_rir = sum(actual_rir_values) / len(actual_rir_values)
    increment = prescription.weight_increment or Decimal("0")

    if actual_rir > target_rir:
        return ProgressionResult(
            ProgressionAction.INCREASE,
            latest.weight + increment,
            f"Target RIR {target_rir}, actual {actual_rir:g} — more in reserve than planned, "
            f"adding {increment} kg.",
            len(attempts),
        )

    if actual_rir <= target_rir - 2:
        deload_weight = (latest.weight * DELOAD_FACTOR).quantize(TWO_PLACES)
        return ProgressionResult(
            ProgressionAction.DECREASE,
            deload_weight,
            f"Target RIR {target_rir}, actual {actual_rir:g} — notably closer to failure than "
            f"planned, easing back to {deload_weight} kg.",
            len(attempts),
        )

    return ProgressionResult(
        ProgressionAction.MAINTAIN,
        latest.weight,
        f"Target RIR {target_rir}, actual {actual_rir:g} — close enough to plan, hold the "
        "weight.",
        len(attempts),
    )


_HANDLERS = {
    ProgressionMethod.MANUAL: _manual,
    ProgressionMethod.MAINTENANCE: _maintenance,
    ProgressionMethod.LINEAR: _linear,
    ProgressionMethod.DOUBLE_PROGRESSION: _double_progression,
    ProgressionMethod.REP_RANGE: _rep_range,
    ProgressionMethod.PERCENTAGE_BASED: _percentage_based,
    ProgressionMethod.RPE_RIR: _rpe_rir,
}
