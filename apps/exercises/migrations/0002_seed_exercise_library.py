"""Seed system-owned lookup data and a starter exercise library.

Muscle groups and equipment are fixed lookup data (see docs/DOMAIN_MODEL.md),
not user-created, so they're seeded here rather than through the admin. A
small set of common barbell/dumbbell/machine/bodyweight exercises is seeded
too, so a fresh install has a usable library instead of an empty one; users
can still add their own custom exercises on top.
"""

from django.db import migrations

MUSCLE_GROUPS = [
    "Chest",
    "Back",
    "Shoulders",
    "Biceps",
    "Triceps",
    "Quads",
    "Hamstrings",
    "Glutes",
    "Calves",
    "Abs",
    "Forearms",
]

EQUIPMENT = [
    "Barbell",
    "Dumbbell",
    "Machine",
    "Cable",
    "Bodyweight",
    "Kettlebell",
    "Resistance Band",
]

# (name, movement_type, weight_input_mode, equipment, primary[], secondary[])
EXERCISES = [
    ("Barbell Back Squat", "compound", "total", "Barbell", ["Quads"], ["Glutes", "Hamstrings"]),
    ("Barbell Bench Press", "compound", "total", "Barbell", ["Chest"], ["Triceps", "Shoulders"]),
    ("Conventional Deadlift", "compound", "total", "Barbell", ["Back", "Hamstrings"], ["Glutes", "Forearms"]),
    ("Overhead Press", "compound", "total", "Barbell", ["Shoulders"], ["Triceps"]),
    ("Barbell Row", "compound", "total", "Barbell", ["Back"], ["Biceps"]),
    ("Pull-Up", "compound", "total", "Bodyweight", ["Back"], ["Biceps"]),
    ("Dumbbell Bench Press", "compound", "per_hand", "Dumbbell", ["Chest"], ["Triceps", "Shoulders"]),
    ("Dumbbell Shoulder Press", "compound", "per_hand", "Dumbbell", ["Shoulders"], ["Triceps"]),
    ("Dumbbell Bicep Curl", "isolation", "per_hand", "Dumbbell", ["Biceps"], []),
    ("Triceps Pushdown", "isolation", "total", "Cable", ["Triceps"], []),
    ("Lat Pulldown", "compound", "total", "Cable", ["Back"], ["Biceps"]),
    ("Leg Press", "compound", "total", "Machine", ["Quads"], ["Glutes", "Hamstrings"]),
    ("Leg Curl", "isolation", "total", "Machine", ["Hamstrings"], []),
    ("Calf Raise", "isolation", "total", "Machine", ["Calves"], []),
    ("Plank", "isolation", "total", "Bodyweight", ["Abs"], []),
]


def seed(apps, schema_editor):
    MuscleGroup = apps.get_model("exercises", "MuscleGroup")
    Equipment = apps.get_model("exercises", "Equipment")
    Exercise = apps.get_model("exercises", "Exercise")

    muscle_groups = {
        name: MuscleGroup.objects.get_or_create(name=name)[0] for name in MUSCLE_GROUPS
    }
    equipment = {
        name: Equipment.objects.get_or_create(name=name)[0] for name in EQUIPMENT
    }

    for name, movement_type, weight_input_mode, equipment_name, primary, secondary in EXERCISES:
        exercise, _ = Exercise.objects.get_or_create(
            name=name,
            owner=None,
            defaults={
                "movement_type": movement_type,
                "weight_input_mode": weight_input_mode,
                "equipment": equipment.get(equipment_name),
            },
        )
        exercise.primary_muscle_groups.set(muscle_groups[m] for m in primary)
        exercise.secondary_muscle_groups.set(muscle_groups[m] for m in secondary)


def unseed(apps, schema_editor):
    Exercise = apps.get_model("exercises", "Exercise")
    MuscleGroup = apps.get_model("exercises", "MuscleGroup")
    Equipment = apps.get_model("exercises", "Equipment")
    Exercise.objects.filter(owner=None, name__in=[e[0] for e in EXERCISES]).delete()
    MuscleGroup.objects.filter(name__in=MUSCLE_GROUPS).delete()
    Equipment.objects.filter(name__in=EQUIPMENT).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("exercises", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
