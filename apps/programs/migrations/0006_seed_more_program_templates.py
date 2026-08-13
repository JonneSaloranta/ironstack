"""Seed two more built-in program templates, on request.

Depends on apps.exercises' 0004 seed migration for the newer exercises
"Upper/Lower Split" prescribes.
"""

from django.db import migrations

# (name, description, [(workout_name, weekday_or_None, [(exercise_name, sets, min_reps, max_reps, method), ...]), ...])
TEMPLATES = [
    (
        "Upper/Lower Split (4-Day)",
        (
            "A 4-day split alternating upper-body and lower-body sessions "
            "twice each per week — a common middle ground between full-body "
            "and single-muscle-group routines, popular for balancing "
            "strength and volume without training every day."
        ),
        [
            (
                "Upper A",
                None,
                [
                    ("Barbell Bench Press", 4, 5, 8, "linear"),
                    ("Barbell Row", 4, 6, 10, "double_progression"),
                    ("Overhead Press", 3, 6, 10, "double_progression"),
                    ("Lat Pulldown", 3, 8, 12, "double_progression"),
                    ("Dumbbell Bicep Curl", 3, 10, 12, "double_progression"),
                ],
            ),
            (
                "Lower A",
                None,
                [
                    ("Barbell Back Squat", 4, 5, 8, "linear"),
                    ("Romanian Deadlift", 3, 6, 10, "double_progression"),
                    ("Leg Press", 3, 8, 12, "double_progression"),
                    ("Calf Raise", 4, 12, 15, "double_progression"),
                ],
            ),
            (
                "Upper B",
                None,
                [
                    ("Incline Barbell Bench Press", 4, 6, 10, "double_progression"),
                    ("Seated Cable Row", 4, 8, 12, "double_progression"),
                    ("Dumbbell Shoulder Press", 3, 8, 12, "double_progression"),
                    ("Face Pull", 3, 12, 15, "double_progression"),
                    ("Skull Crusher", 3, 10, 12, "double_progression"),
                ],
            ),
            (
                "Lower B",
                None,
                [
                    ("Front Squat", 4, 6, 10, "double_progression"),
                    ("Hip Thrust", 3, 8, 12, "double_progression"),
                    ("Leg Curl", 3, 10, 12, "double_progression"),
                    ("Calf Raise", 4, 12, 15, "double_progression"),
                ],
            ),
        ],
    ),
    (
        "German Volume Training",
        (
            "The classic 10x10 high-volume method: one main lift per "
            "workout done for ten sets of ten reps at a fixed, moderate "
            "weight, paired with a lighter accessory movement. Brutal on "
            "paper, simple in practice — built for muscle growth, not "
            "1-rep-max testing."
        ),
        [
            (
                "Workout A",
                None,
                [
                    ("Barbell Back Squat", 10, 10, 10, "manual"),
                    ("Leg Curl", 3, 10, 12, "double_progression"),
                ],
            ),
            (
                "Workout B",
                None,
                [
                    ("Barbell Bench Press", 10, 10, 10, "manual"),
                    ("Barbell Row", 10, 10, 10, "manual"),
                ],
            ),
        ],
    ),
]


def seed(apps, schema_editor):
    Program = apps.get_model("programs", "Program")
    Workout = apps.get_model("programs", "Workout")
    ExercisePrescription = apps.get_model("programs", "ExercisePrescription")
    Exercise = apps.get_model("exercises", "Exercise")

    for name, description, workouts in TEMPLATES:
        program, created = Program.objects.get_or_create(
            name=name,
            owner=None,
            defaults={"description": description, "is_template": True},
        )
        if not created:
            continue

        for order, (workout_name, weekday, prescriptions) in enumerate(workouts):
            workout = Workout.objects.create(
                program=program, name=workout_name, order=order, scheduled_weekday=weekday
            )
            for p_order, (exercise_name, sets, min_reps, max_reps, method) in enumerate(
                prescriptions
            ):
                ExercisePrescription.objects.create(
                    workout=workout,
                    exercise=Exercise.objects.get(name=exercise_name, owner=None),
                    order=p_order,
                    set_count=sets,
                    min_reps=min_reps,
                    max_reps=max_reps,
                    progression_method=method,
                )


def unseed(apps, schema_editor):
    Program = apps.get_model("programs", "Program")
    Program.objects.filter(
        owner=None, name__in=[name for name, _description, _workouts in TEMPLATES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0005_alter_exerciseprescription_max_reps_and_more"),
        ("exercises", "0004_seed_more_exercises"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
