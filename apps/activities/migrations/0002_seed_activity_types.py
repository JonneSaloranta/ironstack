"""Seed a reasonable common set of system activity types. Unlike
apps.measurements.MeasurementType, docs/DOMAIN_MODEL.md doesn't specify an
exact list for activities ("users may create custom activity types") —
this is a starting point users are free to extend.
"""

from django.db import migrations

ACTIVITY_TYPES = [
    "Running",
    "Walking",
    "Cycling",
    "Swimming",
    "Hiking",
    "Rowing",
    "Yoga",
    "Other",
]


def seed(apps, schema_editor):
    ActivityType = apps.get_model("activities", "ActivityType")
    for name in ACTIVITY_TYPES:
        ActivityType.objects.get_or_create(name=name, owner=None)


def unseed(apps, schema_editor):
    ActivityType = apps.get_model("activities", "ActivityType")
    ActivityType.objects.filter(owner=None, name__in=ACTIVITY_TYPES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("activities", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
