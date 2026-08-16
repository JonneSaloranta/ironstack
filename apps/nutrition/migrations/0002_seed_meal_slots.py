"""Seed the default meal slots — see docs/NUTRITION.md "MealSlot"."""

from django.db import migrations

MEAL_SLOTS = [
    ("Breakfast", 0),
    ("Lunch", 1),
    ("Dinner", 2),
    ("Evening snack", 3),
]


def seed(apps, schema_editor):
    MealSlot = apps.get_model("nutrition", "MealSlot")
    for name, order in MEAL_SLOTS:
        MealSlot.objects.get_or_create(name=name, owner=None, defaults={"order": order})


def unseed(apps, schema_editor):
    MealSlot = apps.get_model("nutrition", "MealSlot")
    MealSlot.objects.filter(owner=None, name__in=[name for name, _ in MEAL_SLOTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
