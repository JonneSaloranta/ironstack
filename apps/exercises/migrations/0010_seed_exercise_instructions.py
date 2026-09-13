"""Populates `Exercise.instructions` (and, where sourced externally,
`instructions_attribution`) for the seeded system library — the same
27-of-28 exercises migration 0007 attached an image to; "Side Plank"
is skipped here too, its own only available source text carrying a
third-party (bodybuilding.com) attribution rather than an actual
wger.de community contribution, unlike every other entry used here.

Most of these come from the same source as the images (wger.de's
public exercise database, `exercise-translation` endpoint, CC-BY-SA)
— cleaned up from HTML into plain text, with machine-translation
boilerplate ("Translated with DeepL.com...") and dead inline links
stripped. A handful of the source entries turned out unusable as
actual instructions, either too thin to explain the movement at all
("To do slowly, tempo is 4010", the entirety of one) or describing the
wrong equipment for this project's own seeded exercise (a "dumbbell"
Face Pull/Hip Thrust description on what this library seeds as a Cable/
Barbell exercise respectively) — those were rewritten from scratch
instead of shipping incorrect or useless instructions, and carry no
`instructions_attribution` since they're original text, not wger's.
See `seed_data/exercise_instructions.json` for the exact source per
exercise.

Idempotent — skips an exercise that already has instructions — so
re-running `migrate` (or a test database rebuild) never overwrites a
later hand-edit.
"""

import json
from pathlib import Path

from django.db import migrations

DATA_PATH = Path(__file__).resolve().parent.parent / "seed_data" / "exercise_instructions.json"


def seed(apps, schema_editor):
    Exercise = apps.get_model("exercises", "Exercise")

    data = json.loads(DATA_PATH.read_text())

    for exercise_name, info in data.items():
        try:
            exercise = Exercise.objects.get(name=exercise_name, owner=None)
        except Exercise.DoesNotExist:
            continue
        if exercise.instructions:
            continue
        exercise.instructions = info["instructions"]
        exercise.instructions_attribution = info.get("attribution", "")
        exercise.save(update_fields=["instructions", "instructions_attribution"])


def unseed(apps, schema_editor):
    Exercise = apps.get_model("exercises", "Exercise")
    data = json.loads(DATA_PATH.read_text())
    Exercise.objects.filter(owner=None, name__in=data.keys()).update(
        instructions="", instructions_attribution=""
    )


class Migration(migrations.Migration):

    dependencies = [
        ('exercises', '0009_exercise_instructions_attribution'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
