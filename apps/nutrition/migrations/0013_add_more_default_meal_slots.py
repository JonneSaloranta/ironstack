"""Add a snack slot between each pair of main meals, plus a catch-all
"Other" slot — see docs/NUTRITION.md "MealSlot". The original seed
(0002_seed_meal_slots) only had one snack slot, after Dinner; logging
something eaten between breakfast and lunch, or between lunch and
dinner, had to be miscategorized into a main meal, and food that
genuinely fit none of the slots had nowhere honest to go either.
"""

from django.db import migrations

MEAL_SLOT_ORDER = [
    ("Breakfast", 0),
    ("Morning snack", 1),
    ("Lunch", 2),
    ("Afternoon snack", 3),
    ("Dinner", 4),
    ("Evening snack", 5),
    ("Other", 6),
]

NEW_SLOT_NAMES = {"Morning snack", "Afternoon snack", "Other"}


def seed(apps, schema_editor):
    MealSlot = apps.get_model("nutrition", "MealSlot")
    for name, order in MEAL_SLOT_ORDER:
        MealSlot.objects.update_or_create(
            name=name, owner=None, defaults={"order": order}
        )


def unseed(apps, schema_editor):
    MealSlot = apps.get_model("nutrition", "MealSlot")
    MealSlot.objects.filter(owner=None, name__in=NEW_SLOT_NAMES).delete()
    for name, order in [("Breakfast", 0), ("Lunch", 1), ("Dinner", 2), ("Evening snack", 3)]:
        MealSlot.objects.filter(owner=None, name=name).update(order=order)


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0012_tag_template_recipes_with_meal_slot"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
