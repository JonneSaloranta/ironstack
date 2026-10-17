"""Swaps the em-dash for a plain hyphen in the seeded template recipe
names, so they match their (now hyphenated) translation msgids again.

Built-in content only; text a user wrote stays as they typed it.
"""

from django.db import migrations
from django.db.models import Value
from django.db.models.functions import Replace

EM_DASH = "\u2014"


def replace_em_dashes(apps, schema_editor):
    Recipe = apps.get_model("nutrition", "Recipe")
    Recipe.objects.filter(owner=None, name__contains=EM_DASH).update(
        name=Replace("name", Value(EM_DASH), Value("-"))
    )


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0026_diaryentry_nutrition_snapshot"),
    ]

    operations = [
        migrations.RunPython(replace_em_dashes, migrations.RunPython.noop),
    ]
