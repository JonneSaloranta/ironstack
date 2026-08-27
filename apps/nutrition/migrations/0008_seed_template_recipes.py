"""Seed built-in template recipes — one small library of ready-to-log
meals per goal (bulk / fatburner / balanced) x meal (breakfast / lunch
/ dinner), so a fresh install has something to browse and log on day
one instead of an empty recipe list. See docs/NUTRITION.md "Recipe".

Ingredient Food rows are seeded with real OpenFoodFacts data (fetched
once, by hand, while writing this migration — the same off_id/name/
brand/macros a live `import_or_refresh_food_from_off` call would
produce for that exact product) rather than the migration itself
calling OFF over the network: a migration that depends on network
access and a third-party service being up is a fragile thing to put in
the one code path every fresh install and every upgrade must run
through. `off_synced_at=now()` means these behave exactly like a
normal import from here on — eligible for the usual 14-day staleness
refresh (apps.nutrition.services.OPENFOODFACTS_STALENESS_DAYS) the
next time one of them is used, same as any other imported Food.

Recipe/Food names and Recipe.instructions here are the same canonical
English strings apps.nutrition.i18n_content lists for `makemessages` —
see that module's own docstring for why the stored value must stay
untranslated while the display goes through `{% trans %}` at render
time. The ingredient Food names are real OFF product names/brands and,
like every other OFF-imported Food, are never translated at all.
"""

from decimal import Decimal

from django.db import migrations
from django.utils import timezone

# (off_id, name, brand, calories, protein_g, carb_g, fat_g) — all per
# 100g, OpenFoodFacts' own universal unit, fetched live from
# world.openfoodfacts.org while writing this migration.
INGREDIENTS = {
    "oats": ("3168930003632", "Quaker Oats", "Quaker", 375, "11", "60", "8"),
    "eggs": ("5051140150471", "Free range British eggs", "Tesco", 131, "12.6", "0", "9"),
    "banana": ("00040112", "Fresh Banana - Each", "", 78, "0.87", "18", "0.26"),
    "peanut_butter": (
        "0037600106009", "Creamy Peanut Butter", "Skippy", 605, "26.9", "16.6", "43.4",
    ),
    "bread": (
        "0072250037129", "100% Whole Wheat Bread", "Nature's Own", 214, "11.5", "50", "1.9",
    ),
    "chicken": (
        "29304707", "Chicken Breast Fillets Skinless", "M&S Food", 108, "24.3", "0.1", "1.2",
    ),
    "rice": (
        "0807176541005", "Cooked Sticky White Rice Medium Grain", "bibigo",
        148, "2.86", "33.8", "0.48",
    ),
    "salmon": ("0889396000265", "Sockeye Salmon Fillets", "Raw Seafoods", 150, "22.12", "0", "7.08"),
    "sweet_potato": (
        "0087738172100", "Raw Sweet Potato Fries", "Fresh Produce", 82, "1.18", "20", "0",
    ),
    "broccoli": ("00512947", "Broccoli Florets", "Sainsbury's", 43, "4.3", "3.2", "0.6"),
    "spinach": ("0060383030827", "Chopped Spinach", "No Name", 40, "4", "5.33", "0.67"),
    "greek_yogurt": (
        "5201054017432", "Total 5% Fat Greek Yogurt", "FAGE", 93, "9", "3", "5",
    ),
    "cottage_cheese": ("3033491922466", "Cottage Cheese", "Danone", 88, "12", "1.4", "3.3"),
    "tuna": (
        "0096619977819", "Albacore Solid White Tuna in Water", "Kirkland",
        124, "42", "0", "0.98",
    ),
    "almonds": ("3760181141226", "Amandes decortiquees", "Initia Food", 616, "22", "13", "52"),
    "olive_oil": ("8005510007961", "Extra Virgin Olive Oil", "Monini", 828, "0", "0", "92"),
}

# (name, servings, instructions, [(ingredient_key, quantity_grams), ...])
RECIPES = [
    (
        "Bulk breakfast — Oats & peanut butter", 1,
        "Mix the oats into the yogurt, top with banana slices and peanut butter.",
        [("oats", "90"), ("banana", "120"), ("peanut_butter", "25"), ("greek_yogurt", "150")],
    ),
    (
        "Bulk breakfast — Eggs on toast", 1,
        "Fry or scramble the eggs, serve on toasted bread with peanut butter and a "
        "drizzle of olive oil.",
        [("eggs", "150"), ("bread", "90"), ("peanut_butter", "20"), ("olive_oil", "10")],
    ),
    (
        "Bulk lunch — Chicken & rice bowl", 1,
        "Cook the chicken and rice, toss with olive oil, serve with steamed broccoli.",
        [("chicken", "200"), ("rice", "300"), ("olive_oil", "15"), ("broccoli", "100")],
    ),
    (
        "Bulk lunch — Tuna & sweet potato", 1,
        "Roast the sweet potato, flake in the tuna, drizzle with olive oil and serve "
        "with spinach.",
        [("tuna", "150"), ("sweet_potato", "300"), ("olive_oil", "15"), ("spinach", "80")],
    ),
    (
        "Bulk dinner — Salmon & rice", 1,
        "Pan-sear the salmon, serve over rice with steamed broccoli and a little "
        "olive oil.",
        [("salmon", "200"), ("rice", "250"), ("olive_oil", "10"), ("broccoli", "120")],
    ),
    (
        "Bulk dinner — Chicken & sweet potato", 1,
        "Roast the chicken and sweet potato together, top with crushed almonds and "
        "olive oil.",
        [("chicken", "220"), ("sweet_potato", "280"), ("almonds", "20"), ("olive_oil", "10")],
    ),
    (
        "Fatburner breakfast — Egg & spinach scramble", 1,
        "Scramble the eggs with the spinach, serve with cottage cheese on the side.",
        [("eggs", "150"), ("spinach", "100"), ("cottage_cheese", "100")],
    ),
    (
        "Fatburner breakfast — Greek yogurt bowl", 1,
        "Top the yogurt with sliced banana and a few crushed almonds.",
        [("greek_yogurt", "200"), ("banana", "80"), ("almonds", "10")],
    ),
    (
        "Fatburner lunch — Chicken & broccoli", 1,
        "Grill the chicken, steam the broccoli, finish with a light drizzle of olive oil.",
        [("chicken", "200"), ("broccoli", "200"), ("olive_oil", "5")],
    ),
    (
        "Fatburner lunch — Tuna & spinach salad", 1,
        "Toss the tuna with the spinach and a small drizzle of olive oil.",
        [("tuna", "150"), ("spinach", "100"), ("olive_oil", "5")],
    ),
    (
        "Fatburner dinner — Salmon & greens", 1,
        "Bake the salmon, serve with steamed broccoli and spinach.",
        [("salmon", "150"), ("broccoli", "200"), ("spinach", "80")],
    ),
    (
        "Fatburner dinner — Chicken & cottage cheese", 1,
        "Grill the chicken, serve with cottage cheese and wilted spinach.",
        [("chicken", "150"), ("cottage_cheese", "150"), ("spinach", "100")],
    ),
    (
        "Balanced breakfast — Oats & yogurt", 1,
        "Mix the oats into the yogurt and top with sliced banana.",
        [("oats", "60"), ("greek_yogurt", "150"), ("banana", "100")],
    ),
    (
        "Balanced breakfast — Eggs on toast", 1,
        "Fry or scramble the eggs, serve on toasted bread with a little olive oil.",
        [("eggs", "100"), ("bread", "60"), ("olive_oil", "5")],
    ),
    (
        "Balanced lunch — Chicken & rice", 1,
        "Cook the chicken and rice, serve with steamed broccoli and olive oil.",
        [("chicken", "150"), ("rice", "180"), ("broccoli", "100"), ("olive_oil", "8")],
    ),
    (
        "Balanced lunch — Tuna sandwich", 1,
        "Mix the tuna with the spinach, serve between slices of toasted bread.",
        [("tuna", "100"), ("bread", "90"), ("spinach", "40")],
    ),
    (
        "Balanced dinner — Salmon & sweet potato", 1,
        "Bake the salmon and sweet potato, serve with wilted spinach.",
        [("salmon", "150"), ("sweet_potato", "200"), ("spinach", "60")],
    ),
    (
        "Balanced dinner — Chicken & vegetables", 1,
        "Roast the chicken with the broccoli and sweet potato, finish with olive oil.",
        [("chicken", "150"), ("broccoli", "150"), ("sweet_potato", "100"), ("olive_oil", "8")],
    ),
]


def seed(apps, schema_editor):
    Food = apps.get_model("nutrition", "Food")
    Recipe = apps.get_model("nutrition", "Recipe")
    RecipeIngredient = apps.get_model("nutrition", "RecipeIngredient")
    now = timezone.now()

    foods_by_key = {}
    for key, (off_id, name, brand, calories, protein, carb, fat) in INGREDIENTS.items():
        food, _created = Food.objects.get_or_create(
            off_id=off_id,
            defaults=dict(
                owner=None,
                name=name,
                brand=brand,
                serving_size=Decimal("100"),
                serving_unit="g",
                calories=calories,
                protein_grams=Decimal(protein),
                carbohydrate_grams=Decimal(carb),
                fat_grams=Decimal(fat),
                off_synced_at=now,
            ),
        )
        foods_by_key[key] = food

    for name, servings, instructions, ingredients in RECIPES:
        recipe, _created = Recipe.objects.get_or_create(
            owner=None,
            name=name,
            defaults=dict(servings=servings, instructions=instructions),
        )
        for order, (key, quantity) in enumerate(ingredients):
            RecipeIngredient.objects.get_or_create(
                recipe=recipe,
                food=foods_by_key[key],
                defaults=dict(quantity=Decimal(quantity), order=order),
            )


def unseed(apps, schema_editor):
    Recipe = apps.get_model("nutrition", "Recipe")
    Food = apps.get_model("nutrition", "Food")
    Recipe.objects.filter(owner=None, name__in=[name for name, *_ in RECIPES]).delete()
    Food.objects.filter(off_id__in=[off_id for off_id, *_ in INGREDIENTS.values()]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0007_alter_recipe_owner"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
