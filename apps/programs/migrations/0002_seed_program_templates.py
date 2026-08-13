"""Seed one built-in program template so a fresh install has something to
copy from (see docs/PRODUCT_REQUIREMENTS.md: "use built-in program
templates"). Depends on apps.exercises' seed migration for the exercises
it prescribes.
"""

from django.db import migrations

# (workout_name, scheduled_weekday, [(exercise_name, sets, min_reps, max_reps, progression_method), ...])
WORKOUTS = [
    (
        "Workout A",
        0,  # Monday
        [
            ("Barbell Back Squat", 3, 5, 5, "linear"),
            ("Barbell Bench Press", 3, 5, 5, "linear"),
            ("Barbell Row", 3, 8, 10, "double_progression"),
        ],
    ),
    (
        "Workout B",
        2,  # Wednesday
        [
            ("Conventional Deadlift", 1, 5, 5, "linear"),
            ("Overhead Press", 3, 5, 5, "linear"),
            ("Lat Pulldown", 3, 8, 12, "double_progression"),
        ],
    ),
    (
        "Workout C",
        4,  # Friday
        [
            ("Barbell Back Squat", 3, 5, 5, "linear"),
            ("Dumbbell Bench Press", 3, 8, 12, "double_progression"),
            ("Dumbbell Bicep Curl", 3, 10, 12, "double_progression"),
            ("Triceps Pushdown", 3, 10, 12, "double_progression"),
        ],
    ),
]


def seed(apps, schema_editor):
    Program = apps.get_model("programs", "Program")
    Workout = apps.get_model("programs", "Workout")
    ExercisePrescription = apps.get_model("programs", "ExercisePrescription")
    Exercise = apps.get_model("exercises", "Exercise")

    program, created = Program.objects.get_or_create(
        name="Full Body A/B/C",
        owner=None,
        defaults={
            "description": (
                "A classic three-day full-body template: one squat/press/row "
                "day, one deadlift/press/pull day, and one accessory-focused "
                "day. Copy it and adjust weights/increments to your own."
            ),
            "is_template": True,
        },
    )
    if not created:
        return

    for order, (workout_name, weekday, prescriptions) in enumerate(WORKOUTS):
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
    Program.objects.filter(name="Full Body A/B/C", owner=None).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0001_initial"),
        ("exercises", "0002_seed_exercise_library"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
