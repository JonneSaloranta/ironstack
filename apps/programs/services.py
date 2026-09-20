"""Program domain logic kept out of views, per CLAUDE.md.

Visibility rules and template copying live here so later phases (workout
logging) can reuse the same "which programs can this user use" query
instead of re-deriving it.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q

from apps.core.data_exchange import ImportValidationError

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


def _decimal_or_none(value):
    """Best-effort parse for an optional numeric field coming from an
    untrusted import file — `None`/missing/garbage all just come back
    as `None` rather than raising, since every field this is used for
    (target_weight/target_rpe/weight_increment/percentage_target) is
    itself optional on `ExercisePrescription`. A single malformed
    optional number in an otherwise-good file shouldn't fail the whole
    import — see apps.core.data_exchange's own module docstring."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _exercise_export_payload(exercise):
    """One `ExercisePrescription.exercise` as a natural-key dict —
    just `name`/`is_system` for a *system* exercise (matched by name
    on import, since `unique_system_exercise_name` guarantees that's
    unambiguous and every instance's system library comes from the
    same seed migrations), or every field needed to recreate it for a
    *custom* one, which won't exist at all on whatever instance
    eventually imports this."""
    if exercise.owner_id is None:
        return {"name": exercise.name, "is_system": True}
    return {
        "name": exercise.name,
        "is_system": False,
        "description": exercise.description,
        "instructions": exercise.instructions,
        "movement_type": exercise.movement_type,
        "weight_input_mode": exercise.weight_input_mode,
        "equipment": exercise.equipment.name if exercise.equipment_id else None,
        "primary_muscle_groups": list(
            exercise.primary_muscle_groups.order_by("name").values_list("name", flat=True)
        ),
        "secondary_muscle_groups": list(
            exercise.secondary_muscle_groups.order_by("name").values_list("name", flat=True)
        ),
    }


def export_program(program):
    """A `Program` (with its workouts/prescriptions/exercises), as a
    plain dict ready for `apps.core.data_exchange.build_envelope` —
    see that module's own docstring for why this isn't just
    `apps.api.serializers.ProgramSerializer`. `is_template`/`owner`
    are deliberately left out entirely: an imported program is always
    a fresh, ordinary program owned by whoever imports it, never
    something that silently becomes a shared system template on
    someone else's instance."""
    return {
        "name": program.name,
        "description": program.description,
        "workouts": [
            {
                "name": workout.name,
                "order": workout.order,
                "scheduled_weekday": workout.scheduled_weekday,
                "notes": workout.notes,
                "prescriptions": [
                    {
                        "order": prescription.order,
                        "set_count": prescription.set_count,
                        "min_reps": prescription.min_reps,
                        "max_reps": prescription.max_reps,
                        "target_weight_kg": (
                            str(prescription.target_weight)
                            if prescription.target_weight is not None
                            else None
                        ),
                        "target_rpe": (
                            str(prescription.target_rpe)
                            if prescription.target_rpe is not None
                            else None
                        ),
                        "target_rir": prescription.target_rir,
                        "progression_method": prescription.progression_method,
                        "weight_increment_kg": (
                            str(prescription.weight_increment)
                            if prescription.weight_increment is not None
                            else None
                        ),
                        "percentage_target": (
                            str(prescription.percentage_target)
                            if prescription.percentage_target is not None
                            else None
                        ),
                        "notes": prescription.notes,
                        "exercise": _exercise_export_payload(prescription.exercise),
                    }
                    for prescription in workout.prescriptions.select_related(
                        "exercise", "exercise__equipment"
                    ).prefetch_related(
                        "exercise__primary_muscle_groups", "exercise__secondary_muscle_groups"
                    ).order_by("order", "id")
                ],
            }
            for workout in program.workouts.order_by("order", "id")
        ],
    }


def _resolve_exercise(user, data):
    """Finds (or, for a custom one, creates) the `Exercise` a
    prescription's own exported payload points at — see
    `_exercise_export_payload`'s own docstring for the system/custom
    split this mirrors. A system exercise this importing instance
    doesn't actually have (an older/smaller seed library, or one
    since renamed) falls back to creating it as the importing user's
    own custom exercise instead of failing the whole program import
    over one missing library entry — `import_program` would rather
    hand back a program with a slightly-off exercise than nothing."""
    from apps.exercises.models import Equipment, Exercise, MuscleGroup

    name = data.get("name")
    if not name:
        raise ImportValidationError("A prescription is missing its exercise name.")

    if data.get("is_system", True):
        exercise = Exercise.objects.filter(owner__isnull=True, name=name, active=True).first()
        if exercise is not None:
            return exercise

    exercise = Exercise.objects.filter(owner=user, name=name, active=True).first()
    if exercise is not None:
        return exercise

    equipment = None
    equipment_name = data.get("equipment")
    if equipment_name:
        equipment = Equipment.objects.filter(name=equipment_name).first()

    exercise = Exercise.objects.create(
        owner=user,
        name=name,
        description=data.get("description", ""),
        instructions=data.get("instructions", ""),
        movement_type=data.get("movement_type") or "compound",
        weight_input_mode=data.get("weight_input_mode") or "total",
        equipment=equipment,
    )
    for group_name in data.get("primary_muscle_groups") or []:
        group = MuscleGroup.objects.filter(name=group_name).first()
        if group is not None:
            exercise.primary_muscle_groups.add(group)
    for group_name in data.get("secondary_muscle_groups") or []:
        group = MuscleGroup.objects.filter(name=group_name).first()
        if group is not None:
            exercise.secondary_muscle_groups.add(group)
    return exercise


@transaction.atomic
def import_program(user, payload):
    """The inverse of `export_program` — creates a brand new `Program`
    owned by `user` from a previously-exported payload. Wrapped in one
    transaction: any `ImportValidationError` partway through (a
    missing exercise name, for instance) rolls back everything already
    written, so an import either fully succeeds or leaves no trace at
    all, never a half-built program with some workouts missing."""
    name = payload.get("name")
    if not name:
        raise ImportValidationError("Missing program name.")

    program = Program.objects.create(
        owner=user,
        name=name,
        description=payload.get("description", ""),
        is_template=False,
    )
    for workout_data in payload.get("workouts") or []:
        workout = Workout.objects.create(
            program=program,
            name=workout_data.get("name", ""),
            order=workout_data.get("order", 0),
            scheduled_weekday=workout_data.get("scheduled_weekday"),
            notes=workout_data.get("notes", ""),
        )
        prescriptions = []
        for order, prescription_data in enumerate(workout_data.get("prescriptions") or []):
            exercise = _resolve_exercise(user, prescription_data.get("exercise") or {})
            prescriptions.append(
                ExercisePrescription(
                    workout=workout,
                    exercise=exercise,
                    order=prescription_data.get("order", order),
                    set_count=prescription_data.get("set_count", 3),
                    min_reps=prescription_data.get("min_reps", 8),
                    max_reps=prescription_data.get("max_reps", 12),
                    target_weight=_decimal_or_none(prescription_data.get("target_weight_kg")),
                    target_rpe=_decimal_or_none(prescription_data.get("target_rpe")),
                    target_rir=prescription_data.get("target_rir"),
                    progression_method=prescription_data.get("progression_method") or "manual",
                    weight_increment=_decimal_or_none(
                        prescription_data.get("weight_increment_kg")
                    ),
                    percentage_target=_decimal_or_none(
                        prescription_data.get("percentage_target")
                    ),
                    notes=prescription_data.get("notes", ""),
                )
            )
        ExercisePrescription.objects.bulk_create(prescriptions)
    return program
