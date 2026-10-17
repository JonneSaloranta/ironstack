"""Swaps the em-dash for a plain hyphen in the seeded image and
instruction attributions, matching the hyphen the seed data now uses.

Built-in content only; text a user wrote stays as they typed it.
"""

from django.db import migrations
from django.db.models import Value
from django.db.models.functions import Replace

EM_DASH = "\u2014"


def replace_em_dashes(apps, schema_editor):
    for model_name, field in (
        ("Exercise", "instructions_attribution"),
        ("ExerciseImage", "attribution"),
    ):
        model = apps.get_model("exercises", model_name)
        model.objects.filter(**{f"{field}__contains": EM_DASH}).update(
            **{field: Replace(field, Value(EM_DASH), Value("-"))}
        )


class Migration(migrations.Migration):

    dependencies = [
        ("exercises", "0011_exerciseimage_dimensions"),
    ]

    operations = [
        migrations.RunPython(replace_em_dashes, migrations.RunPython.noop),
    ]
