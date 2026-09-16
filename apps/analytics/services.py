"""Analytics queries — kept out of views/templates per CLAUDE.md ("do not
put analytics logic in templates"). Straightforward ORM queries per
docs/ANALYTICS.md ("start with straightforward ORM queries and indexes...
only add denormalized/cached aggregates when profiling demonstrates a
need") — nothing here is persisted or cached.

Every query is scoped to a single user and, where dated, to a DateRange
(apps.analytics.dateranges) — docs/ANALYTICS.md: "analytics must always
respect user ownership."
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from apps.core import units as core_units
from apps.core.charts import build_bar_series, build_chart_series
from apps.records.models import PersonalRecord, PRType
from apps.records.one_rep_max import OneRepMaxCalculator
from apps.workouts.models import ExerciseSet, WorkoutSession, WorkoutSessionStatus

_one_rep_max = OneRepMaxCalculator()


def _weight_display(value, user):
    """Convert a canonical-kg volume/1RM figure to `user`'s preferred
    display unit before it's staged into a summary or chart — the same
    "convert before building the series" convention apps.measurements
    uses, so a chart's own min/max/point values are already in the unit
    its label claims, not just re-labeled kg. See apps.core.units."""
    return core_units.kg_to_display(value, getattr(user, "unit_system", "metric"))


def _completed_sessions(user, date_range):
    qs = WorkoutSession.objects.filter(user=user, status=WorkoutSessionStatus.COMPLETED)
    if date_range.start:
        qs = qs.filter(started_at__date__gte=date_range.start)
    return qs.filter(started_at__date__lte=date_range.end)


def _training_sets(user, date_range, *, exercise=None):
    """Sets that count toward training-load analytics: real working sets
    from completed sessions. Unlike apps.records' PR eligibility, a
    failed set still counts here — the work still happened, even if it
    didn't "successfully" set a record."""
    qs = ExerciseSet.objects.filter(
        performed_exercise__session__user=user,
        performed_exercise__session__status=WorkoutSessionStatus.COMPLETED,
        is_warmup=False,
    ).select_related("performed_exercise__session", "performed_exercise__exercise")
    if exercise is not None:
        qs = qs.filter(performed_exercise__exercise=exercise)
    if date_range.start:
        qs = qs.filter(performed_exercise__session__started_at__date__gte=date_range.start)
    return qs.filter(performed_exercise__session__started_at__date__lte=date_range.end)


@dataclass(frozen=True)
class TrainingSummary:
    session_count: int
    total_duration: timedelta
    total_volume: Decimal


def _training_summary(user, date_range, *, convert) -> TrainingSummary:
    sessions = list(_completed_sessions(user, date_range))
    total_duration = sum(
        (s.ended_at - s.started_at for s in sessions if s.ended_at), timedelta()
    )
    total_volume = sum(
        (s.weight * s.reps for s in _training_sets(user, date_range)), Decimal("0")
    )
    return TrainingSummary(
        session_count=len(sessions),
        total_duration=total_duration,
        total_volume=_weight_display(total_volume, user) if convert else total_volume,
    )


def training_summary(user, date_range) -> TrainingSummary:
    return _training_summary(user, date_range, convert=True)


def training_summary_canonical(user, date_range) -> TrainingSummary:
    """Same figures as `training_summary`, but `total_volume` stays
    canonical kg rather than converting to the user's display unit —
    for apps.api, which never applies that conversion on any endpoint
    (see apps.api.serializers' own docstring for why: an unambiguous
    unit a machine caller can rely on regardless of who's asking, not a
    human-facing preference)."""
    return _training_summary(user, date_range, convert=False)


def weekly_volume_series(user, date_range):
    """Bar chart: total training volume per ISO week —
    docs/ANALYTICS.md "bar chart: weekly training volume"."""
    weekly = {}
    for exercise_set in _training_sets(user, date_range):
        session_date = exercise_set.performed_exercise.session.started_at.date()
        week_start = session_date - timedelta(days=session_date.weekday())
        weekly[week_start] = weekly.get(week_start, Decimal("0")) + (
            exercise_set.weight * exercise_set.reps
        )
    ordered = sorted(weekly.items())
    return build_bar_series(
        [(week.strftime("%b %d"), _weight_display(volume, user)) for week, volume in ordered]
    )


def muscle_group_volume_series(user, date_range):
    """Bar chart: total volume attributed to each primary muscle group —
    docs/ANALYTICS.md "bar chart: muscle-group volume". A set with
    multiple primary muscle groups counts its full volume toward each —
    simplest defensible split, rather than dividing volume fractionally
    with no principled basis for the split."""
    sets = _training_sets(user, date_range).prefetch_related(
        "performed_exercise__exercise__primary_muscle_groups"
    )
    totals = {}
    for exercise_set in sets:
        volume = exercise_set.weight * exercise_set.reps
        for muscle_group in exercise_set.performed_exercise.exercise.primary_muscle_groups.all():
            totals[muscle_group.name] = totals.get(muscle_group.name, Decimal("0")) + volume
    ordered = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return build_bar_series([(label, _weight_display(volume, user)) for label, volume in ordered])


def pr_history(user, date_range, limit=None):
    """docs/ANALYTICS.md "PR history" — every PR achieved in range, most
    recent first. Reuses apps.records' immutable achievement log rather
    than recomputing anything."""
    qs = PersonalRecord.objects.filter(user=user).select_related("exercise")
    if date_range.start:
        qs = qs.filter(achieved_at__date__gte=date_range.start)
    qs = qs.filter(achieved_at__date__lte=date_range.end)
    return qs[:limit] if limit else qs


@dataclass(frozen=True)
class ExercisePRGroup:
    """One exercise's rows from a single `pr_history()` call, still in
    that call's own most-recent-first order within each list —
    templates/core/dashboard.html and templates/analytics/dashboard.html's
    own "Recent PRs" group by exercise rather than rendering one card
    per row, since `PersonalRecord` logs a separate row per
    `record_type` (docs/PR_SYSTEM.md), and hitting several types for
    the same exercise in one session used to read as several
    near-identical cards rather than one box for that exercise.

    Split into `primary_records` (shown directly on the card) and
    `secondary_records` (shown only once expanded) rather than one
    flat list — `_PRIMARY_RECORD_TYPES` below is exactly the set
    `apps.records.views.ExerciseRecordsView`'s own "Current PRs" `<dl>`
    already treats as the headline figures (max weight, estimated 1RM,
    best set/session volume); rep-specific and plain rep PRs are that
    same page's own separate, secondary "Rep maxes"/"Rep records by
    weight" sections, reused here as "expand for more" instead."""

    exercise: object
    primary_records: list
    secondary_records: list


# apps.records.views.ExerciseRecordsView's own "Current PRs" grouping
# (templates/records/exercise_records.html) — see ExercisePRGroup's
# own docstring for why the same split applies here.
_PRIMARY_RECORD_TYPES = frozenset(
    {PRType.MAX_WEIGHT, PRType.ESTIMATED_1RM, PRType.SET_VOLUME, PRType.SESSION_VOLUME}
)


def pr_history_grouped_by_exercise(user, date_range, limit=None):
    """`pr_history(user, date_range, limit)`, grouped by exercise and
    split into `ExercisePRGroup.primary_records`/`secondary_records`
    afterward — not a second query, and not a reordering: a plain
    `dict` preserves insertion order, and since the input is already
    most-recent-first, the first row seen for a given exercise is, by
    definition, that exercise's own most recent — so the resulting
    groups come out in exactly the order that exercise's most recent
    PR would have sorted to on its own, with no separate sort step
    needed, and each group's own two lists keep that same relative
    order too.

    An exercise whose only PR(s) in range are secondary-type (e.g. a
    lone rep-specific PR, no max weight/1RM/volume hit) still needs
    *something* to show without expanding — a card that's just a name
    and a chevron reads as broken, not minimal — so a group with no
    primary records falls back to treating its secondary ones as
    primary instead."""
    per_exercise = {}
    for record in pr_history(user, date_range, limit=limit):
        exercise, records = per_exercise.setdefault(record.exercise_id, (record.exercise, []))
        records.append(record)

    groups = []
    for exercise, records in per_exercise.values():
        primary = [r for r in records if r.record_type in _PRIMARY_RECORD_TYPES]
        secondary = [r for r in records if r.record_type not in _PRIMARY_RECORD_TYPES]
        if not primary:
            primary, secondary = secondary, primary
        groups.append(
            ExercisePRGroup(exercise=exercise, primary_records=primary, secondary_records=secondary)
        )
    return groups


@dataclass(frozen=True)
class ExerciseAnalyticsSummary:
    session_count: int
    total_volume: Decimal


def exercise_summary(user, exercise, date_range) -> ExerciseAnalyticsSummary:
    sets = list(_training_sets(user, date_range, exercise=exercise))
    session_ids = {s.performed_exercise.session_id for s in sets}
    total_volume = sum((s.weight * s.reps for s in sets), Decimal("0"))
    return ExerciseAnalyticsSummary(
        session_count=len(session_ids), total_volume=_weight_display(total_volume, user)
    )


def exercise_one_rm_trend(user, exercise, date_range):
    """Line chart: estimated 1RM over time — docs/ANALYTICS.md "line
    chart: estimated 1RM over time" / "line chart: exercise strength
    trend". One point per session (that session's best estimate), not
    per set, so the trend stays readable."""
    sets = (
        ExerciseSet.objects.filter(
            performed_exercise__exercise=exercise,
            performed_exercise__session__user=user,
            performed_exercise__session__status=WorkoutSessionStatus.COMPLETED,
            is_warmup=False,
            is_failure=False,
        )
        .select_related("performed_exercise__session")
    )
    if date_range.start:
        sets = sets.filter(performed_exercise__session__started_at__date__gte=date_range.start)
    sets = sets.filter(performed_exercise__session__started_at__date__lte=date_range.end)

    best_per_session = {}
    for exercise_set in sets:
        session_date = exercise_set.performed_exercise.session.started_at.date()
        estimate = _one_rep_max.estimate(exercise_set.weight, exercise_set.reps)
        if session_date not in best_per_session or estimate > best_per_session[session_date]:
            best_per_session[session_date] = estimate

    readings = [(_weight_display(value, user), date) for date, value in best_per_session.items()]
    return build_chart_series(readings)
