"""PR detection - kept out of views/templates per CLAUDE.md.

`check_and_record_prs` is the entry point: called once, right after a new
`ExerciseSet` is saved (see apps.workouts.views.set_log), it compares that
set against the user's prior history for the exercise and records any
newly broken personal records. Only called for newly *created* sets -
editing or deleting a set instead goes through `rebuild_records`, which
replays the exercise's whole remaining history (docs/PR_SYSTEM.md
"Correcting a logged set").

Every "current best" here is computed live from `ExerciseSet` history each
time, not read back from previously stored `PersonalRecord` rows - so
detection is correct regardless of what's already stored, and program
edits/deletions (which this module never queries) can't affect it.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Max, Sum

from apps.core import units as core_units

from .models import PersonalRecord, PRType
from .one_rep_max import OneRepMaxCalculator

REP_MILESTONES = [1, 3, 5, 8, 10, 12]

# weight (max_digits=6, decimal_places=2) * reps (a plain integer) -
# 12 digits comfortably covers even a whole session's worth of these
# summed together (_best_session_volume below), well past any
# realistic single value. Shared so a set's volume is computed the
# same way whether it's one set (previous_volumes) or summed across a
# session (_best_session_volume) - both push this into the database
# rather than fetching every row into Python to multiply by hand,
# which used to mean re-scanning a whole exercise's history on every
# single set logged (see this module's own docstring).
_VOLUME_EXPRESSION = ExpressionWrapper(
    F("weight") * F("reps"), output_field=DecimalField(max_digits=12, decimal_places=2)
)

_one_rep_max = OneRepMaxCalculator()


def eligible_sets(user, exercise):
    """Sets that count toward PRs: real, completed effort only - see
    docs/DOMAIN_MODEL.md on warmup sets, and PR_SYSTEM.md's "largest
    weight ever *successfully* recorded" for why failures are excluded
    too."""
    from apps.workouts.models import ExerciseSet

    return ExerciseSet.objects.filter(
        performed_exercise__exercise=exercise,
        performed_exercise__session__user=user,
        is_warmup=False,
        is_failure=False,
    )


def current_records(user, exercise):
    """The user's current best for each PR type on this exercise, computed
    live from history. Returns a dict keyed by PRType value; rep_specific
    entries are nested under their own dict keyed by rep count. Used both
    by PR detection (to find the "previous best") and by any UI wanting to
    show "your current PRs" without needing stored PersonalRecord rows."""
    qs = eligible_sets(user, exercise)
    max_weight = qs.aggregate(Max("weight"))["weight__max"]
    best_estimate = None
    set_volume = None
    for weight, reps in qs.values_list("weight", "reps"):
        estimate = _one_rep_max.estimate(weight, reps)
        if best_estimate is None or estimate > best_estimate:
            best_estimate = estimate
        volume = weight * reps
        if set_volume is None or volume > set_volume:
            set_volume = volume
    return {
        PRType.MAX_WEIGHT: max_weight,
        PRType.ESTIMATED_1RM: best_estimate,
        PRType.SET_VOLUME: set_volume,
        PRType.SESSION_VOLUME: _best_session_volume(qs),
        PRType.REP_SPECIFIC_PR: {
            milestone: _best_weight_for_at_least_reps(qs, milestone)
            for milestone in REP_MILESTONES
        },
        PRType.REP_PR: {
            weight: _best_reps_at_weight(qs, weight)
            for weight in qs.order_by("weight").values_list("weight", flat=True).distinct()
        },
    }


def _best_weight_for_at_least_reps(qs, rep_count, exclude_pk=None):
    filtered = qs.filter(reps__gte=rep_count)
    if exclude_pk is not None:
        filtered = filtered.exclude(pk=exclude_pk)
    return filtered.aggregate(Max("weight"))["weight__max"]


def _best_reps_at_weight(qs, weight, exclude_pk=None):
    filtered = qs.filter(weight=weight)
    if exclude_pk is not None:
        filtered = filtered.exclude(pk=exclude_pk)
    return filtered.aggregate(Max("reps"))["reps__max"]


def _session_volume(qs, session_id):
    total = Decimal("0")
    for weight, reps in qs.filter(performed_exercise__session_id=session_id).values_list(
        "weight", "reps"
    ):
        total += weight * reps
    return total


def _best_session_volume(qs, exclude_session_id=None):
    """The highest total volume (sum of weight*reps across every set) any
    one session has ever hit for this (user, exercise). Grouped and
    summed in the database (one query, however many sessions of history
    exist) rather than pulling every eligible set into Python to total
    by hand - `qs` is `eligible_sets(user, exercise)`, unbounded by
    date range, so a long-time user's whole history for a favorite
    exercise (thousands of sets, run again on every single new set
    logged - see this module's own docstring) used to mean fetching
    and re-summing all of it here on every set."""
    values = qs
    if exclude_session_id is not None:
        values = values.exclude(performed_exercise__session_id=exclude_session_id)
    result = (
        values.values("performed_exercise__session_id")
        .annotate(total=Sum(_VOLUME_EXPRESSION))
        .aggregate(best=Max("total"))
    )
    return result["best"]


def check_and_record_prs(exercise_set):
    """Check one freshly-logged set against history; create+return any
    newly broken `PersonalRecord`s. Each returned record additionally
    carries a transient `.previous_value` (not persisted) for notification
    display - see docs/PR_SYSTEM.md's "New PR / Previous ..." example."""
    if exercise_set.is_warmup or exercise_set.is_failure:
        return []

    performed_exercise = exercise_set.performed_exercise
    session = performed_exercise.session
    user = session.user
    exercise = performed_exercise.exercise
    qs = eligible_sets(user, exercise)

    new_records = []

    def _record(record_type, value, *, rep_count=None, previous=None):
        record = PersonalRecord.objects.create(
            user=user,
            exercise=exercise,
            record_type=record_type,
            rep_count=rep_count,
            value=value,
            weight=exercise_set.weight,
            reps=exercise_set.reps,
            source_set=exercise_set,
            achieved_at=exercise_set.performed_at,
        )
        record.previous_value = previous
        new_records.append(record)

    previous_max = qs.exclude(pk=exercise_set.pk).aggregate(Max("weight"))["weight__max"]
    if previous_max is None or exercise_set.weight > previous_max:
        _record(PRType.MAX_WEIGHT, exercise_set.weight, previous=previous_max)

    previous_reps = _best_reps_at_weight(qs, exercise_set.weight, exclude_pk=exercise_set.pk)
    if previous_reps is None or exercise_set.reps > previous_reps:
        _record(PRType.REP_PR, exercise_set.reps, previous=previous_reps)

    for milestone in REP_MILESTONES:
        if exercise_set.reps >= milestone:
            previous_weight = _best_weight_for_at_least_reps(
                qs, milestone, exclude_pk=exercise_set.pk
            )
            if previous_weight is None or exercise_set.weight > previous_weight:
                _record(
                    PRType.REP_SPECIFIC_PR,
                    exercise_set.weight,
                    rep_count=milestone,
                    previous=previous_weight,
                )

    estimate = _one_rep_max.estimate(exercise_set.weight, exercise_set.reps)
    previous_estimates = [
        _one_rep_max.estimate(weight, reps)
        for weight, reps in qs.exclude(pk=exercise_set.pk).values_list("weight", "reps")
    ]
    previous_best_estimate = max(previous_estimates, default=None)
    if previous_best_estimate is None or estimate > previous_best_estimate:
        _record(PRType.ESTIMATED_1RM, estimate, previous=previous_best_estimate)

    set_volume = exercise_set.weight * exercise_set.reps
    # Max(weight*reps) computed in the database rather than pulling
    # every prior set's (weight, reps) pair into Python to multiply by
    # hand - same reasoning as _best_session_volume's own docstring.
    previous_best_volume = qs.exclude(pk=exercise_set.pk).aggregate(
        best=Max(_VOLUME_EXPRESSION)
    )["best"]
    if previous_best_volume is None or set_volume > previous_best_volume:
        _record(PRType.SET_VOLUME, set_volume, previous=previous_best_volume)

    session_record = _upsert_session_volume_record(qs, user, exercise, exercise_set, session)
    if session_record is not None:
        new_records.append(session_record)

    return new_records


def _upsert_session_volume_record(qs, user, exercise, exercise_set, session):
    """Session volume is a running total, not a single set's raw number -
    later sets in the *same* session update this row instead of each
    firing their own "new PR", per PersonalRecord's docstring."""
    total = _session_volume(qs, session.id)
    best_other_session = _best_session_volume(qs, exclude_session_id=session.id)
    beats_history = best_other_session is None or total > best_other_session

    existing = PersonalRecord.objects.filter(
        user=user,
        exercise=exercise,
        record_type=PRType.SESSION_VOLUME,
        source_set__performed_exercise__session_id=session.id,
    ).first()

    if existing:
        if beats_history:
            existing.value = total
            existing.weight = exercise_set.weight
            existing.reps = exercise_set.reps
            existing.source_set = exercise_set
            existing.achieved_at = exercise_set.performed_at
            existing.save()
        return None  # already notified once for this session

    if beats_history:
        record = PersonalRecord.objects.create(
            user=user,
            exercise=exercise,
            record_type=PRType.SESSION_VOLUME,
            value=total,
            weight=exercise_set.weight,
            reps=exercise_set.reps,
            source_set=exercise_set,
            achieved_at=exercise_set.performed_at,
        )
        record.previous_value = best_other_session
        return record

    return None


def _replayed_records(user, exercise):
    """The records `check_and_record_prs` would have produced had every
    remaining eligible set been logged in `performed_at` order, keyed the
    way `rebuild_records` matches them against stored rows: (set id,
    type, rep count), or ("session", session id) for session volume,
    whose one row per session follows the session's latest record-
    breaking set (same as `_upsert_session_volume_record`). One query,
    replayed in Python - the same comparisons, strict `>` everywhere, so
    a tie is still never a record."""
    rows = (
        eligible_sets(user, exercise)
        .order_by("performed_at", "pk")
        .values_list("pk", "weight", "reps", "performed_at", "performed_exercise__session_id")
    )
    desired = {}
    max_weight = best_estimate = best_volume = None
    best_reps_at = {}
    best_weight_for = {}
    session_totals = {}

    def _beats(value, previous):
        return previous is None or value > previous

    for pk, weight, reps, performed_at, session_id in rows:
        fields = {
            "weight": weight,
            "reps": reps,
            "source_set_id": pk,
            "achieved_at": performed_at,
        }

        if _beats(weight, max_weight):
            desired[(pk, PRType.MAX_WEIGHT, None)] = {**fields, "value": weight}
            max_weight = weight

        if _beats(reps, best_reps_at.get(weight)):
            desired[(pk, PRType.REP_PR, None)] = {**fields, "value": reps}
            best_reps_at[weight] = reps

        for milestone in REP_MILESTONES:
            if reps >= milestone and _beats(weight, best_weight_for.get(milestone)):
                desired[(pk, PRType.REP_SPECIFIC_PR, milestone)] = {**fields, "value": weight}
                best_weight_for[milestone] = weight

        estimate = _one_rep_max.estimate(weight, reps)
        if _beats(estimate, best_estimate):
            desired[(pk, PRType.ESTIMATED_1RM, None)] = {**fields, "value": estimate}
            best_estimate = estimate

        volume = weight * reps
        if _beats(volume, best_volume):
            desired[(pk, PRType.SET_VOLUME, None)] = {**fields, "value": volume}
            best_volume = volume

        total = session_totals.get(session_id, Decimal("0")) + volume
        session_totals[session_id] = total
        best_other = max(
            (other for key, other in session_totals.items() if key != session_id),
            default=None,
        )
        if _beats(total, best_other):
            desired[("session", session_id)] = {**fields, "value": total}

    return desired


def _record_key(record):
    if record.record_type == PRType.SESSION_VOLUME:
        if record.source_set is None:
            return None
        return ("session", record.source_set.performed_exercise.session_id)
    return (record.source_set_id, record.record_type, record.rep_count)


@transaction.atomic
def rebuild_records(user, exercise):
    """Bring `user`'s stored records for `exercise` back in line with
    their remaining set history, after a set was edited or deleted (or a
    whole session was). A record that still holds keeps its row
    untouched; one whose set is gone (or no longer beats what came
    before it) is deleted; a later set that only now turns out to have
    been a record - because the mistake it was compared against is gone
    - gets its row, silently (no "new PR" toast for an old set). See
    docs/PR_SYSTEM.md "Correcting a logged set" and issue #21: a 600 kg
    typo, deleted right away, used to stay the max weight forever."""
    desired = _replayed_records(user, exercise)
    stored = PersonalRecord.objects.filter(user=user, exercise=exercise).select_related(
        "source_set__performed_exercise"
    )
    for record in stored:
        fields = desired.pop(_record_key(record), None)
        if fields is None:
            record.delete()
            continue
        if any(getattr(record, name) != value for name, value in fields.items()):
            for name, value in fields.items():
                setattr(record, name, value)
            record.save()

    PersonalRecord.objects.bulk_create(
        PersonalRecord(
            user=user,
            exercise=exercise,
            record_type=PRType.SESSION_VOLUME if key[0] == "session" else key[1],
            rep_count=None if key[0] == "session" else key[2],
            **fields,
        )
        for key, fields in desired.items()
    )


def rebuild_records_for_exercises(user, exercise_ids):
    """`rebuild_records` for each exercise in `exercise_ids` - for a
    change that touches several at once, like deleting a session."""
    from apps.exercises.models import Exercise

    for exercise in Exercise.objects.filter(pk__in=set(exercise_ids)):
        rebuild_records(user, exercise)


def display_value(record, user):
    """A `PersonalRecord`'s headline `.value`, unit-converted for
    `user`'s preferred unit - every record type except `rep_pr`, whose
    value is a plain rep count, not a weight (see `PersonalRecord`'s
    docstring for which types mean what). Returns the bare number; use
    `format_value` for a display string that also carries the unit.
    """
    if record.record_type == PRType.REP_PR:
        return record.value
    return core_units.kg_to_display(record.value, getattr(user, "unit_system", "metric"))


def display_previous_value(record, user):
    """Same conversion as `display_value`, for the transient
    `.previous_value` a freshly-checked PR carries."""
    previous = getattr(record, "previous_value", None)
    if previous is None:
        return None
    if record.record_type == PRType.REP_PR:
        return previous
    return core_units.kg_to_display(previous, getattr(user, "unit_system", "metric"))


def _formatted(record, value, user):
    if value is None:
        return ""
    if record.record_type == PRType.REP_PR:
        return str(value)
    unit_system = getattr(user, "unit_system", "metric")
    return f"{value} {core_units.weight_unit_label(unit_system)}"


def format_value(record, user):
    """`display_value`, suffixed with the right unit (e.g. "225.0 lb")
    or bare for a rep count. Shared by the recent-PRs templates
    (apps.records.templatetags.records_extras) and the "New PR" flash
    message (apps.workouts.views.set_log) so both agree on the same
    conversion instead of each re-implementing it - regression: that
    message used to interpolate the raw kg value directly, unconverted
    and unlabeled, even for an imperial-preference user.
    """
    return _formatted(record, display_value(record, user), user)


def format_previous_value(record, user):
    """Same formatting as `format_value`, for the transient
    `.previous_value` a freshly-checked PR carries."""
    return _formatted(record, display_previous_value(record, user), user)
