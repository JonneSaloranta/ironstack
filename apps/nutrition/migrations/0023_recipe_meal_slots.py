from django.db import migrations, models


def copy_meal_slot_to_meal_slots(apps, schema_editor):
    Recipe = apps.get_model("nutrition", "Recipe")
    for recipe in Recipe.objects.exclude(meal_slot__isnull=True):
        recipe.meal_slots.add(recipe.meal_slot_id)


def copy_first_meal_slot_back(apps, schema_editor):
    Recipe = apps.get_model("nutrition", "Recipe")
    for recipe in Recipe.objects.all():
        first = recipe.meal_slots.order_by("order", "pk").first()
        if first is not None:
            recipe.meal_slot = first
            recipe.save(update_fields=["meal_slot"])


class Migration(migrations.Migration):
    """Recipe.meal_slot (one meal) → Recipe.meal_slots (any number),
    keeping every recipe's existing tag."""

    dependencies = [
        ("nutrition", "0022_food_full_nutrition_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="recipe",
            name="meal_slots",
            field=models.ManyToManyField(blank=True, related_name="+", to="nutrition.mealslot"),
        ),
        migrations.RunPython(copy_meal_slot_to_meal_slots, copy_first_meal_slot_back),
        migrations.RemoveField(model_name="recipe", name="meal_slot"),
    ]
