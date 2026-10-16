from decimal import Decimal

from django.db import migrations, models

_FIELDS = (
    "calories", "protein_grams", "carbohydrate_grams", "fat_grams",
    "fiber_grams", "sugar_grams", "saturated_fat_grams", "sodium_mg",
)


def _per_unit_food(food):
    """One of the food's own units — the same scaling as
    services.scale_nutrition(food, 1), kept here so later changes to the
    service can't change what this migration did."""
    ratio = Decimal("1") / food.serving_size
    values = {}
    for name in _FIELDS:
        value = getattr(food, name)
        values[name] = None if value is None else Decimal(value) * ratio
    return values


def _per_serving_recipe(recipe, RecipeIngredient):
    total = {name: None for name in _FIELDS}
    for name in ("calories", "protein_grams", "carbohydrate_grams", "fat_grams"):
        total[name] = Decimal("0")
    for ingredient in RecipeIngredient.objects.filter(recipe=recipe).select_related("food"):
        for name, value in _per_unit_food(ingredient.food).items():
            if value is None:
                continue
            total[name] = (total[name] or Decimal("0")) + value * ingredient.quantity
    servings = Decimal(recipe.servings) if recipe.servings else Decimal("1")
    return {name: None if v is None else v / servings for name, v in total.items()}


def snapshot_existing_entries(apps, schema_editor):
    """Freeze every existing food/recipe entry at today's values — the
    best record left of what it was when logged. From here on a later
    edit of the food or recipe no longer changes these days."""
    DiaryEntry = apps.get_model("nutrition", "DiaryEntry")
    RecipeIngredient = apps.get_model("nutrition", "RecipeIngredient")
    recipe_cache = {}
    entries = DiaryEntry.objects.filter(
        nutrition_snapshot__isnull=True, quick_calories__isnull=True
    ).select_related("food", "recipe")
    for entry in entries.iterator():
        if entry.food_id:
            values = _per_unit_food(entry.food)
        elif entry.recipe_id:
            if entry.recipe_id not in recipe_cache:
                recipe_cache[entry.recipe_id] = _per_serving_recipe(entry.recipe, RecipeIngredient)
            values = recipe_cache[entry.recipe_id]
        else:
            continue
        entry.nutrition_snapshot = {
            name: None if value is None else str(value) for name, value in values.items()
        }
        entry.save(update_fields=["nutrition_snapshot"])


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0025_recipe_soft_delete"),
    ]

    operations = [
        migrations.AddField(
            model_name="diaryentry",
            name="nutrition_snapshot",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(snapshot_existing_entries, migrations.RunPython.noop),
    ]
