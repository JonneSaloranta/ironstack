"""Seed the system measurement types listed in docs/DOMAIN_MODEL.md."""

from django.db import migrations

MEASUREMENT_TYPES = [
    ("Body weight", "weight"),
    ("Body fat %", "percentage"),
    ("Waist", "length"),
    ("Chest", "length"),
    ("Arm", "length"),
    ("Thigh", "length"),
    ("Hip", "length"),
    ("Neck", "length"),
]


def seed(apps, schema_editor):
    MeasurementType = apps.get_model("measurements", "MeasurementType")
    for name, unit_kind in MEASUREMENT_TYPES:
        MeasurementType.objects.get_or_create(
            name=name, owner=None, defaults={"unit_kind": unit_kind}
        )


def unseed(apps, schema_editor):
    MeasurementType = apps.get_model("measurements", "MeasurementType")
    MeasurementType.objects.filter(
        owner=None, name__in=[name for name, _ in MEASUREMENT_TYPES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("measurements", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
