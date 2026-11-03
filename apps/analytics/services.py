"""Analytics queries - kept out of views/templates per CLAUDE.md ("do not
put analytics logic in templates"). Straightforward ORM queries per
docs/ANALYTICS.md ("start with straightforward ORM queries and indexes...
only add denormalized/cached aggregates when profiling demonstrates a
need") - nothing here is persisted or cached.

Every query is scoped to a single user and, where dated, to a DateRange
(apps.analytics.dateranges) - docs/ANALYTICS.md: "analytics must always
respect user ownership."
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from apps.core import units as core_units
from apps.core.charts import build_bar_series, build_chart_series
from apps.measurements import services as measurement_services
from apps.measurements import units as measurement_units
from apps.measurements.models import UnitKind
from apps.records.models import PersonalRecord, PRType
from apps.records.one_rep_max import OneRepMaxCalculator
from apps.workouts.models import ExerciseSet, WorkoutSession, WorkoutSessionStatus

_one_rep_max = OneRepMaxCalculator()


def _weight_display(value, user):
    """Convert a canonical-kg volume/1RM figure to `user`'s preferred
    display unit before it's staged into a summary or chart - the same
    "convert before building the series" convention apps.measurements
    uses, so a chart's own min/max/point values are already in the unit
    its label claims, not just re-labeled kg. See apps.core.units."""
    return core_units.kg_to_display(value, getattr(user, "unit_system", "metric"))


@dataclass(frozen=True)
class BodyMeasurementProgress:
    """One measurement type's row in the Progress page's body card, in
    the user's display unit. `change` is None for a single reading, and
    `change_percent` is None where a relative change means nothing: a
    measurement that is itself a percentage, or a first value of 0."""

    measurement_type: object
    value: Decimal
    unit_label: str
    change: Decimal | None
    change_percent: Decimal | None


def body_measurement_progress(user, date_range) -> list[BodyMeasurementProgress]:
    """How each measurement changed from its first reading in the range
    to its latest. Readings are compared as logged, not smoothed, so the
    figures match what the user entered. The display values are rounded
    before subtracting so a row's own figures always add up."""
    one_place = Decimal("0.1")
    rows = []
    for progress in measurement_services.progress_in_range(
        user, date_range.start, date_range.end
    ):
        unit_kind = progress.measurement_type.unit_kind

        def display(value, unit_kind=unit_kind):
            return measurement_units.to_display(value, unit_kind, user.unit_system).quantize(
                one_place
            )

        latest = display(progress.latest_value)
        change = None
        change_percent = None
        if progress.entry_count > 1:
            change = latest - display(progress.first_value)
            if unit_kind != UnitKind.PERCENTAGE and progress.first_value:
                change_percent = (
                    (progress.latest_value - progress.first_value)
                    / progress.first_value
                    * 100
                ).quantize(one_place)
        rows.append(
            BodyMeasurementProgress(
                measurement_type=progress.measurement_type,
                value=latest,
                unit_label=measurement_units.display_unit_label(unit_kind, user.unit_system),
                change=change,
                change_percent=change_percent,
            )
        )
    return rows


def _completed_sessions(user, date_range):
    qs = WorkoutSession.objects.filter(user=user, status=WorkoutSessionStatus.COMPLETED)
    if date_range.start:
        qs = qs.filter(started_at__date__gte=date_range.start)
    return qs.filter(started_at__date__lte=date_range.end)


def _training_sets(user, date_range, *, exercise=None):
    """Sets that count toward training-load analytics: real working sets
    from completed sessions. Unlike apps.records' PR eligibility, a
    failed set still counts here - the work still happened, even if it
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
    canonical kg rather than converting to the user's display unit -
    for apps.api, which never applies that conversion on any endpoint
    (see apps.api.serializers' own docstring for why: an unambiguous
    unit a machine caller can rely on regardless of who's asking, not a
    human-facing preference)."""
    return _training_summary(user, date_range, convert=False)


def weekly_volume(user, date_range):
    """`(week_start, volume)` per ISO week with training in range, oldest
    first, volume in canonical kg - the data behind weekly_volume_series,
    and what apps.api returns as is."""
    weekly = {}
    for exercise_set in _training_sets(user, date_range):
        session_date = exercise_set.performed_exercise.session.started_at.date()
        week_start = session_date - timedelta(days=session_date.weekday())
        weekly[week_start] = weekly.get(week_start, Decimal("0")) + (
            exercise_set.weight * exercise_set.reps
        )
    return sorted(weekly.items())


def weekly_volume_series(user, date_range):
    """Bar chart: total training volume per ISO week -
    docs/ANALYTICS.md "bar chart: weekly training volume"."""
    return build_bar_series(
        [
            (week.strftime("%b %d"), _weight_display(volume, user))
            for week, volume in weekly_volume(user, date_range)
        ]
    )


def muscle_group_volume(user, date_range):
    """`(muscle_group, volume)` per primary muscle group, largest first,
    volume in canonical kg. A set with multiple primary muscle groups
    counts its full volume toward each - simplest defensible split,
    rather than dividing volume fractionally with no principled basis
    for the split."""
    sets = _training_sets(user, date_range).prefetch_related(
        "performed_exercise__exercise__primary_muscle_groups"
    )
    totals = {}
    for exercise_set in sets:
        volume = exercise_set.weight * exercise_set.reps
        for muscle_group in exercise_set.performed_exercise.exercise.primary_muscle_groups.all():
            totals[muscle_group] = totals.get(muscle_group, Decimal("0")) + volume
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


def muscle_group_volume_series(user, date_range):
    """Bar chart: total volume attributed to each primary muscle group -
    docs/ANALYTICS.md "bar chart: muscle-group volume"."""
    return build_bar_series(
        [
            (muscle_group.name, _weight_display(volume, user))
            for muscle_group, volume in muscle_group_volume(user, date_range)
        ]
    )


def pr_history(user, date_range, limit=None):
    """docs/ANALYTICS.md "PR history" - every PR achieved in range, most
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
    that call's own most-recent-first order within each list -
    templates/core/dashboard.html and templates/analytics/dashboard.html's
    own "Recent PRs" group by exercise rather than rendering one card
    per row, since `PersonalRecord` logs a separate row per
    `record_type` (docs/PR_SYSTEM.md), and hitting several types for
    the same exercise in one session used to read as several
    near-identical cards rather than one box for that exercise.

    Split into `primary_records` (shown directly on the card) and
    `secondary_records` (shown only once expanded) rather than one
    flat list - `_PRIMARY_RECORD_TYPES` below is exactly the set
    `apps.records.views.ExerciseRecordsView`'s own "Current PRs" `<dl>`
    already treats as the headline figures (max weight, estimated 1RM,
    best set/session volume); rep-specific and plain rep PRs are that
    same page's own separate, secondary "Rep maxes"/"Rep records by
    weight" sections, reused here as "expand for more" instead."""

    exercise: object
    primary_records: list
    secondary_records: list


# apps.records.views.ExerciseRecordsView's own "Current PRs" grouping
# (templates/records/exercise_records.html) - see ExercisePRGroup's
# own docstring for why the same split applies here.
_PRIMARY_RECORD_TYPES = frozenset(
    {PRType.MAX_WEIGHT, PRType.ESTIMATED_1RM, PRType.SET_VOLUME, PRType.SESSION_VOLUME}
)


def pr_history_grouped_by_exercise(user, date_range, limit=None):
    """`pr_history(user, date_range, limit)`, grouped by exercise and
    split into `ExercisePRGroup.primary_records`/`secondary_records`
    afterward - not a second query, and not a reordering: a plain
    `dict` preserves insertion order, and since the input is already
    most-recent-first, the first row seen for a given exercise is, by
    definition, that exercise's own most recent - so the resulting
    groups come out in exactly the order that exercise's most recent
    PR would have sorted to on its own, with no separate sort step
    needed, and each group's own two lists keep that same relative
    order too.

    An exercise whose only PR(s) in range are secondary-type (e.g. a
    lone rep-specific PR, no max weight/1RM/volume hit) still needs
    *something* to show without expanding - a card that's just a name
    and a chevron reads as broken, not minimal - so a group with no
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


def _exercise_summary(user, exercise, date_range, *, convert) -> ExerciseAnalyticsSummary:
    sets = list(_training_sets(user, date_range, exercise=exercise))
    session_ids = {s.performed_exercise.session_id for s in sets}
    total_volume = sum((s.weight * s.reps for s in sets), Decimal("0"))
    return ExerciseAnalyticsSummary(
        session_count=len(session_ids),
        total_volume=_weight_display(total_volume, user) if convert else total_volume,
    )


def exercise_summary(user, exercise, date_range) -> ExerciseAnalyticsSummary:
    return _exercise_summary(user, exercise, date_range, convert=True)


def exercise_summary_canonical(user, exercise, date_range) -> ExerciseAnalyticsSummary:
    """`exercise_summary` with `total_volume` in canonical kg, for
    apps.api - see training_summary_canonical."""
    return _exercise_summary(user, exercise, date_range, convert=False)


@dataclass(frozen=True)
class SessionBest:
    """An exercise's best set of one training day, by estimated 1RM -
    canonical kg."""

    date: object
    estimated_one_rep_max: Decimal
    weight: Decimal
    reps: int


def exercise_session_bests(user, exercise, date_range):
    """The best set by estimated 1RM per training day in range, oldest
    first - the data behind exercise_one_rm_trend. Warmup and failed sets
    don't count, like a PR."""
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
        best = best_per_session.get(session_date)
        if best is None or estimate > best.estimated_one_rep_max:
            best_per_session[session_date] = SessionBest(
                date=session_date,
                estimated_one_rep_max=estimate,
                weight=exercise_set.weight,
                reps=exercise_set.reps,
            )
    return [best_per_session[day] for day in sorted(best_per_session)]


def exercise_one_rm_trend(user, exercise, date_range):
    """Line chart: estimated 1RM over time - docs/ANALYTICS.md "line
    chart: estimated 1RM over time" / "line chart: exercise strength
    trend". One point per session (that session's best estimate), not
    per set, so the trend stays readable."""
    readings = [
        (_weight_display(best.estimated_one_rep_max, user), best.date)
        for best in exercise_session_bests(user, exercise, date_range)
    ]
    return build_chart_series(readings)


def trained_exercises(user):
    """Every exercise `user` has logged at least one set for, alphabetical -
    the picker on the Analytics page, so a strength trend is one tap away
    instead of reachable only by first finding the exercise in the library."""
    from apps.exercises.models import Exercise

    logged = ExerciseSet.objects.filter(performed_exercise__session__user=user).values(
        "performed_exercise__exercise"
    )
    return Exercise.objects.filter(pk__in=logged).order_by("name")
