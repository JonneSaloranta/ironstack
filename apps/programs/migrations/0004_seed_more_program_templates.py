"""Seed more well-known built-in program templates, on request — the
original seed (0002) shipped only one generic "Full Body A/B/C". Each of
these describes a real, widely-documented training methodology; naming
one after the lifter who popularized it is standard practice in fitness
literature (the same way a cookbook names a recipe after its chef) and
implies no endorsement or affiliation.

Depends on apps.exercises' seed migration for the exercises prescribed,
same as 0002.
"""

from django.db import migrations

# (name, description, [(workout_name, weekday_or_None, [(exercise_name, sets, min_reps, max_reps, method), ...]), ...])
TEMPLATES = [
    (
        "Arnold Split (6-Day)",
        (
            "The classic 6-day bodybuilding split popularized by Arnold "
            "Schwarzenegger in the 1970s: chest/back, shoulders/arms, and "
            "legs, each trained twice a week. Rotate through all three "
            "workouts, then repeat — not tied to fixed calendar days."
        ),
        [
            (
                "Chest & Back",
                None,
                [
                    ("Barbell Bench Press", 4, 6, 10, "double_progression"),
                    ("Dumbbell Bench Press", 3, 8, 12, "double_progression"),
                    ("Barbell Row", 4, 6, 10, "double_progression"),
                    ("Lat Pulldown", 3, 8, 12, "double_progression"),
                    ("Pull-Up", 3, 5, 10, "double_progression"),
                ],
            ),
            (
                "Shoulders & Arms",
                None,
                [
                    ("Overhead Press", 4, 6, 10, "double_progression"),
                    ("Dumbbell Shoulder Press", 3, 8, 12, "double_progression"),
                    ("Dumbbell Bicep Curl", 3, 10, 12, "double_progression"),
                    ("Triceps Pushdown", 3, 10, 12, "double_progression"),
                ],
            ),
            (
                "Legs",
                None,
                [
                    ("Barbell Back Squat", 4, 6, 10, "double_progression"),
                    ("Leg Press", 3, 8, 12, "double_progression"),
                    ("Leg Curl", 3, 10, 12, "double_progression"),
                    ("Calf Raise", 4, 12, 15, "double_progression"),
                ],
            ),
        ],
    ),
    (
        "Push/Pull/Legs",
        (
            "A widely used 3-day split organized by movement pattern "
            "rather than body part: pushing muscles, pulling muscles, "
            "then legs. Popular for both strength and bodybuilding goals."
        ),
        [
            (
                "Push",
                None,
                [
                    ("Barbell Bench Press", 4, 5, 8, "linear"),
                    ("Overhead Press", 3, 6, 10, "double_progression"),
                    ("Dumbbell Shoulder Press", 3, 8, 12, "double_progression"),
                    ("Triceps Pushdown", 3, 10, 12, "double_progression"),
                ],
            ),
            (
                "Pull",
                None,
                [
                    ("Conventional Deadlift", 3, 5, 5, "linear"),
                    ("Barbell Row", 4, 6, 10, "double_progression"),
                    ("Lat Pulldown", 3, 8, 12, "double_progression"),
                    ("Dumbbell Bicep Curl", 3, 10, 12, "double_progression"),
                ],
            ),
            (
                "Legs",
                None,
                [
                    ("Barbell Back Squat", 4, 5, 8, "linear"),
                    ("Leg Press", 3, 8, 12, "double_progression"),
                    ("Leg Curl", 3, 10, 12, "double_progression"),
                    ("Calf Raise", 4, 12, 15, "double_progression"),
                ],
            ),
        ],
    ),
    (
        "5x5 Strength (A/B)",
        (
            "A classic beginner barbell strength program: two alternating "
            "full-body workouts built around low-rep, high-set compound "
            "lifts with linear weight progression each session."
        ),
        [
            (
                "Workout A",
                0,  # Monday
                [
                    ("Barbell Back Squat", 5, 5, 5, "linear"),
                    ("Barbell Bench Press", 5, 5, 5, "linear"),
                    ("Barbell Row", 5, 5, 5, "linear"),
                ],
            ),
            (
                "Workout B",
                2,  # Wednesday
                [
                    ("Barbell Back Squat", 5, 5, 5, "linear"),
                    ("Overhead Press", 5, 5, 5, "linear"),
                    ("Conventional Deadlift", 1, 5, 5, "linear"),
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
        ("programs", "0003_exerciseprescription_target_weight"),
        ("exercises", "0002_seed_exercise_library"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
