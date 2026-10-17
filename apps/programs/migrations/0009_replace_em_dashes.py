"""Swaps the em-dash for a plain hyphen in the seeded program template
descriptions, so they match their (now hyphenated) translation msgids again.

Built-in content only; text a user wrote stays as they typed it.
"""

from django.db import migrations
from django.db.models import Value
from django.db.models.functions import Replace

EM_DASH = "\u2014"


def replace_em_dashes(apps, schema_editor):
    Program = apps.get_model("programs", "Program")
    Program.objects.filter(owner=None, description__contains=EM_DASH).update(
        description=Replace("description", Value(EM_DASH), Value("-"))
    )


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0008_remove_program_shared_with_clients_and_more"),
    ]

    operations = [
        migrations.RunPython(replace_em_dashes, migrations.RunPython.noop),
    ]
