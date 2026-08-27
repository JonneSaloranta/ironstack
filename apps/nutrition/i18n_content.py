"""Translation catalog for seeded *content*, not UI chrome — the
built-in template recipes' names and instructions
(apps.nutrition.migrations' seed data) are stored in the database in
canonical English, the value `get_or_create(name=...)` matches against
elsewhere, so the stored value itself must never be translated. This
module exists solely so `makemessages` extracts these exact strings
into the `.po` catalog; nothing here is ever imported or executed for
its return value — see apps.exercises.i18n_content and
docs/ARCHITECTURE.md "Internationalization" for the full pattern
(`{% trans recipe.name %}` etc. at render time).

The ingredient Food rows these recipes reference are deliberately
*not* listed here — they're seeded with the same shape a real
OpenFoodFacts import produces (off_id, a real product name/brand), and
exactly like any other OFF-imported Food, that name is never
translated (see apps.nutrition.openfoodfacts.parse_product's own
docstring) — only this app's own authored copy is.
"""

from django.utils.translation import gettext_lazy as _

# Template recipe names (apps.nutrition.migrations' seed data) — the
# name itself says which goal it's built for, per docs/NUTRITION.md.
RECIPE_NAMES = [
    _("Bulk breakfast — Oats & peanut butter"),
    _("Bulk breakfast — Eggs on toast"),
    _("Bulk lunch — Chicken & rice bowl"),
    _("Bulk lunch — Tuna & sweet potato"),
    _("Bulk dinner — Salmon & rice"),
    _("Bulk dinner — Chicken & sweet potato"),
    _("Fatburner breakfast — Egg & spinach scramble"),
    _("Fatburner breakfast — Greek yogurt bowl"),
    _("Fatburner lunch — Chicken & broccoli"),
    _("Fatburner lunch — Tuna & spinach salad"),
    _("Fatburner dinner — Salmon & greens"),
    _("Fatburner dinner — Chicken & cottage cheese"),
    _("Balanced breakfast — Oats & yogurt"),
    _("Balanced breakfast — Eggs on toast"),
    _("Balanced lunch — Chicken & rice"),
    _("Balanced lunch — Tuna sandwich"),
    _("Balanced dinner — Salmon & sweet potato"),
    _("Balanced dinner — Chicken & vegetables"),
]

# Template recipe instructions (same migration, one per name above).
RECIPE_INSTRUCTIONS = [
    _("Mix the oats into the yogurt, top with banana slices and peanut butter."),
    _(
        "Fry or scramble the eggs, serve on toasted bread with peanut butter and a "
        "drizzle of olive oil."
    ),
    _("Cook the chicken and rice, toss with olive oil, serve with steamed broccoli."),
    _("Roast the sweet potato, flake in the tuna, drizzle with olive oil and serve with spinach."),
    _("Pan-sear the salmon, serve over rice with steamed broccoli and a little olive oil."),
    _("Roast the chicken and sweet potato together, top with crushed almonds and olive oil."),
    _("Scramble the eggs with the spinach, serve with cottage cheese on the side."),
    _("Top the yogurt with sliced banana and a few crushed almonds."),
    _("Grill the chicken, steam the broccoli, finish with a light drizzle of olive oil."),
    _("Toss the tuna with the spinach and a small drizzle of olive oil."),
    _("Bake the salmon, serve with steamed broccoli and spinach."),
    _("Grill the chicken, serve with cottage cheese and wilted spinach."),
    _("Mix the oats into the yogurt and top with sliced banana."),
    _("Fry or scramble the eggs, serve on toasted bread with a little olive oil."),
    _("Cook the chicken and rice, serve with steamed broccoli and olive oil."),
    _("Mix the tuna with the spinach, serve between slices of toasted bread."),
    _("Bake the salmon and sweet potato, serve with wilted spinach."),
    _("Roast the chicken with the broccoli and sweet potato, finish with olive oil."),
]
