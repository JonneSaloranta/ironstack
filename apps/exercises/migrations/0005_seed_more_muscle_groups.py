"""Seed a few more commonly-split-out muscle groups, on request — the
original seed (0002) covered 11 broad groups but left out three that
mainstream fitness apps usually list on their own rather than folding
into a broader neighbor: Traps (upper back/neck, distinct enough that
plenty of exercises target only it), Lats (the primary back-width
muscle — "Back" alone doesn't let a user single it out), and Obliques
(side core, distinct from straight Abs work).

Deliberately does not retag any exercise seeded by 0002/0004 (e.g.
"Barbell Row"/"Pull-Up" stay tagged "Back" only, not also "Lats") —
Exercise.primary/secondary_muscle_groups is live, current-state
metadata that analytics reads directly, not a historical snapshot,
so silently changing an existing exercise's muscle groups would
retroactively shift past muscle-group volume charts. Each new group
instead gets one new exercise of its own, so it's immediately usable
rather than an empty option in the muscle-group filter.
"""

from django.db import migrations

MUSCLE_GROUPS = ["Traps", "Lats", "Obliques"]

# (name, movement_type, weight_input_mode, equipment, primary[], secondary[])
EXERCISES = [
    ("Barbell Shrug", "isolation", "total", "Barbell", ["Traps"], []),
    ("Straight-Arm Pulldown", "isolation", "total", "Cable", ["Lats"], []),
    ("Side Plank", "isolation", "total", "Bodyweight", ["Obliques"], []),
]


def seed(apps, schema_editor):
    MuscleGroup = apps.get_model("exercises", "MuscleGroup")
    Equipment = apps.get_model("exercises", "Equipment")
    Exercise = apps.get_model("exercises", "Exercise")

    for name in MUSCLE_GROUPS:
        MuscleGroup.objects.get_or_create(name=name)

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
    MuscleGroup = apps.get_model("exercises", "MuscleGroup")
    Exercise.objects.filter(owner=None, name__in=[e[0] for e in EXERCISES]).delete()
    MuscleGroup.objects.filter(name__in=MUSCLE_GROUPS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("exercises", "0004_seed_more_exercises"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
