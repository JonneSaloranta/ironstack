"""Seed the system stretch library and a handful of ready-made routines
from `seed_data/stretches.json`. The instructions are original text
written for this project (no third-party source to attribute). Names,
descriptions and instructions are stored in canonical English and
translated at render time — see apps.stretching.i18n_content.

Idempotent via get_or_create, so re-running never overwrites a later
hand-edit of a seeded row.
"""

import json
from pathlib import Path

from django.db import migrations

DATA_PATH = Path(__file__).resolve().parent.parent / "seed_data" / "stretches.json"


def seed(apps, schema_editor):
    MuscleGroup = apps.get_model("exercises", "MuscleGroup")
    Stretch = apps.get_model("stretching", "Stretch")
    StretchRoutine = apps.get_model("stretching", "StretchRoutine")
    RoutineItem = apps.get_model("stretching", "RoutineItem")

    data = json.loads(DATA_PATH.read_text())
    muscles = {group.name: group for group in MuscleGroup.objects.all()}

    stretches = {}
    for entry in data["stretches"]:
        stretch, created = Stretch.objects.get_or_create(
            name=entry["name"],
            owner=None,
            defaults={
                "kind": entry["kind"],
                "per_side": entry["per_side"],
                "default_hold_seconds": entry["hold"],
                "instructions": entry["instructions"],
            },
        )
        if created:
            stretch.muscle_groups.set(
                [muscles[name] for name in entry["muscles"] if name in muscles]
            )
        stretches[entry["name"]] = stretch

    for entry in data["routines"]:
        routine, created = StretchRoutine.objects.get_or_create(
            name=entry["name"], owner=None, defaults={"description": entry["description"]}
        )
        if not created:
            continue
        RoutineItem.objects.bulk_create(
            RoutineItem(
                routine=routine,
                stretch=stretches[name],
                order=order,
                hold_seconds=hold,
                sets=sets,
            )
            for order, (name, hold, sets) in enumerate(entry["items"])
        )


def unseed(apps, schema_editor):
    Stretch = apps.get_model("stretching", "Stretch")
    StretchRoutine = apps.get_model("stretching", "StretchRoutine")
    data = json.loads(DATA_PATH.read_text())
    StretchRoutine.objects.filter(
        owner=None, name__in=[entry["name"] for entry in data["routines"]]
    ).delete()
    Stretch.objects.filter(
        owner=None, name__in=[entry["name"] for entry in data["stretches"]]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("stretching", "0001_initial"),
        ("exercises", "0010_seed_exercise_instructions"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
