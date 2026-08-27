"""Tags each built-in template recipe (0008_seed_template_recipes)
with the meal it's actually named for — "Bulk breakfast — Eggs on
toast" gets Breakfast, "Balanced dinner — Chicken & vegetables" gets
Dinner, and so on. Recipe.meal_slot (this migration's own preceding
schema change, 0011) is what apps.nutrition.diet_builder now checks
before suggesting a recipe for a given meal slot — without this
backfill, every template recipe would stay untagged (meal_slot=None,
"any meal") and the exact live bug this exists to fix (a chicken &
rice recipe suggested for breakfast, oats & yogurt for dinner) would
still be possible for all eighteen of them.

Matches by a case-insensitive substring of each recipe's own
canonical (English) name — "breakfast"/"lunch"/"dinner" — rather than
hardcoding the eighteen exact names again: the meal each one is for is
already the second word of its own name (see 0008's own RECIPES list),
so re-deriving it from that name can't drift out of sync with it the
way copy-pasting all eighteen names a second time here could.
"""

from django.db import migrations


def tag(apps, schema_editor):
    Recipe = apps.get_model("nutrition", "Recipe")
    MealSlot = apps.get_model("nutrition", "MealSlot")
    slots_by_keyword = {
        "breakfast": MealSlot.objects.filter(name="Breakfast", owner=None).first(),
        "lunch": MealSlot.objects.filter(name="Lunch", owner=None).first(),
        "dinner": MealSlot.objects.filter(name="Dinner", owner=None).first(),
    }
    for recipe in Recipe.objects.filter(owner=None, meal_slot__isnull=True):
        lowered = recipe.name.lower()
        for keyword, slot in slots_by_keyword.items():
            if slot is not None and keyword in lowered:
                recipe.meal_slot = slot
                recipe.save(update_fields=["meal_slot"])
                break


def untag(apps, schema_editor):
    Recipe = apps.get_model("nutrition", "Recipe")
    Recipe.objects.filter(owner=None).update(meal_slot=None)


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0011_recipe_meal_slot"),
    ]

    operations = [
        migrations.RunPython(tag, untag),
    ]
