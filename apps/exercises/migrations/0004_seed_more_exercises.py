"""Seed more built-in exercises, on request — the original seed (0002)
covered one basic exercise per major movement pattern; this rounds out
the library with common accessory/variation lifts, using only the
muscle groups and equipment 0002 already seeded (no new lookup rows
needed).
"""

from django.db import migrations

# (name, movement_type, weight_input_mode, equipment, primary[], secondary[])
EXERCISES = [
    ("Romanian Deadlift", "compound", "total", "Barbell", ["Hamstrings"], ["Glutes", "Back"]),
    ("Front Squat", "compound", "total", "Barbell", ["Quads"], ["Glutes"]),
    ("Incline Barbell Bench Press", "compound", "total", "Barbell", ["Chest"], ["Shoulders", "Triceps"]),
    ("Hip Thrust", "compound", "total", "Barbell", ["Glutes"], ["Hamstrings"]),
    ("Seated Cable Row", "compound", "total", "Cable", ["Back"], ["Biceps"]),
    ("Face Pull", "isolation", "total", "Cable", ["Shoulders"], ["Back"]),
    ("Lateral Raise", "isolation", "per_hand", "Dumbbell", ["Shoulders"], []),
    ("Hammer Curl", "isolation", "per_hand", "Dumbbell", ["Biceps"], ["Forearms"]),
    ("Skull Crusher", "isolation", "total", "Barbell", ["Triceps"], []),
    ("Ab Wheel Rollout", "isolation", "total", "Bodyweight", ["Abs"], []),
]


def seed(apps, schema_editor):
    MuscleGroup = apps.get_model("exercises", "MuscleGroup")
    Equipment = apps.get_model("exercises", "Equipment")
    Exercise = apps.get_model("exercises", "Exercise")

    for name, movement_type, weight_input_mode, equipment_name, primary, secondary in EXERCISES:
        exercise, _ = Exercise.objects.get_or_create(
            name=name,
            owner=None,
            defaults={
                "movement_type": movement_type,
                "weight_input_mode": weight_input_mode,
                "equipment": Equipment.objects.get(name=equipment_name),
            },
        )
        exercise.primary_muscle_groups.set(
            MuscleGroup.objects.get(name=m) for m in primary
        )
        exercise.secondary_muscle_groups.set(
            MuscleGroup.objects.get(name=m) for m in secondary
        )


def unseed(apps, schema_editor):
    Exercise = apps.get_model("exercises", "Exercise")
    Exercise.objects.filter(owner=None, name__in=[e[0] for e in EXERCISES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("exercises", "0003_alter_exercise_description_alter_exercise_equipment_and_more"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
