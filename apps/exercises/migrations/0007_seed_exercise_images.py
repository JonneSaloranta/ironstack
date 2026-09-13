"""Attach one instructional image to each seeded system exercise that
has a suitable freely-licensed match available (see apps.exercises.
seed_data.exercise_images/manifest.json for source/license/author per
file) — 27 of the library's 28 exercises; "Side Plank" has no good
match in the source library and ships without one, same as any custom
exercise a user never adds an image to.

Images come from wger.de's own public exercise database (https://wger.
de/api/v2/exerciseimage/), a self-hosted, open-source workout tracker
with the same "runs on your own infrastructure" goal as this project —
every image there is licensed CC-BY-SA 3.0/4.0 specifically for reuse
like this, and `manifest.json` records the exact source URL/author/
license this migration credits in each `ExerciseImage.attribution` so
that credit travels with the image rather than living only in this
migration's own history.

The actual image bytes are bundled in this app under `seed_data/
exercise_images/` (checked into git, unlike anything under `media/`,
which is runtime-only and gitignored — see `.gitignore`) and copied
into `MEDIA_ROOT` via `ExerciseImage.image.save()` here, the same way
a real upload would land there. Idempotent — skips an exercise that
already has at least one image — so re-running `migrate` (or a test
database rebuild) never duplicates files on disk. Every file is a
plain JPEG regardless of the format wger originally served it as
(several were actually PNG/WebP despite their own URL's extension) —
one consistent, broadly-compatible format for every bundled image
rather than whatever mix the source happened to use; `manifest.json`'s
own `source_url` keeps the original extension for provenance, only the
locally-bundled `filename` was normalized.
"""

import json
from pathlib import Path

from django.db import migrations

SEED_DIR = Path(__file__).resolve().parent.parent / "seed_data" / "exercise_images"


def seed(apps, schema_editor):
    Exercise = apps.get_model("exercises", "Exercise")
    ExerciseImage = apps.get_model("exercises", "ExerciseImage")

    manifest = json.loads((SEED_DIR / "manifest.json").read_text())

    for exercise_name, info in manifest.items():
        try:
            exercise = Exercise.objects.get(name=exercise_name, owner=None)
        except Exercise.DoesNotExist:
            continue
        if exercise.images.exists():
            continue

        source_path = SEED_DIR / info["filename"]
        if not source_path.exists():
            continue

        image = ExerciseImage(
            exercise=exercise,
            attribution=f"{info['author']} — {info['license']}, via wger.de",
        )
        with source_path.open("rb") as f:
            from django.core.files import File

            image.image.save(info["filename"], File(f), save=True)


def unseed(apps, schema_editor):
    ExerciseImage = apps.get_model("exercises", "ExerciseImage")
    # Only the images this migration itself created carry an
    # `attribution` — a user's own upload for their own custom
    # exercise never has one (see ExerciseImage's own docstring) — so
    # this can't accidentally delete anyone else's data on reversal.
    ExerciseImage.objects.filter(exercise__owner=None, attribution__gt="").delete()


class Migration(migrations.Migration):

    dependencies = [
        ('exercises', '0006_exercise_image_and_settings'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
