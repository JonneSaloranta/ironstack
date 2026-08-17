"""Global template context for the persistent nutrition sub-nav
(`templates/nutrition/_subnav.html`, included from `templates/base.html`
whenever `request.resolver_match.app_name == "nutrition"`).

Why nutrition gets a sub-nav when nothing else in this app does (see
docs/NUTRITION.md "Navigating within nutrition" for the full
reasoning): reported directly as hard to use, and the concrete reason
is scale — nutrition has 7 top-level destinations (dashboard, diary,
foods, recipes, diet plans, calculators, statistics), more than any
other app here, and the plain "one back link per page" convention that
works fine for a 2-3-page flow elsewhere means reaching a sibling
section (e.g. Recipes while on the Diary) requires scrolling back to
the dashboard's own link list first. A persistent, always-reachable
tab bar fixes that directly; nothing else in this app has enough
sibling sections for the same problem to exist.

This context processor only computes *which tab is active* — not
whether the sub-nav renders at all (that's `_subnav.html`'s own
`{% if %}` gate in base.html, since a context processor can't
conditionally skip adding to every request's context, only return an
empty dict cheaply when there's nothing to add).
"""

# One entry per sub-nav tab: the URL names that should highlight it.
# Deliberately whichever *view* the URL resolves to, not the URL
# pattern's path shape — e.g. every diary-related view (viewing a day,
# adding an entry, editing one, copying a day) belongs under "diary",
# not just the bare diary-day view itself.
_TAB_URL_NAMES = {
    "dashboard": {"dashboard"},
    "diary": {
        "diary-day",
        "diary-add-entry",
        "diary-entry-edit",
        "diary-entry-delete",
        "diary-day-copy",
    },
    "foods": {
        "food-list",
        "food-create",
        "food-search",
        "food-browse",
        "food-category",
        "food-import",
    },
    "recipes": {
        "recipe-list",
        "recipe-create",
        "recipe-detail",
        "recipe-update",
        "recipe-delete",
        "recipe-log",
        "recipe-ingredient-create",
        "recipe-ingredient-edit",
        "recipe-ingredient-delete",
    },
    "diet-plans": {
        "diet-plan-list",
        "diet-plan-create",
        "diet-plan-detail",
        "diet-plan-delete",
        "diet-plan-log",
        "diet-plan-item-edit",
        "diet-plan-item-delete",
        "diet-plan-meal-item-add",
    },
    "calculators": {
        "calculators-home",
        "calculator-bmr-tdee",
        "calculator-macros",
        "calculator-body-fat",
        "calculator-water-intake",
        "calculator-bmi",
        "calculator-waist-hip-ratio",
        "calculator-time-to-goal",
    },
    "stats": {"stats"},
}


def nutrition_subnav(request):
    match = getattr(request, "resolver_match", None)
    if match is None or match.app_name != "nutrition":
        return {}
    for tab, url_names in _TAB_URL_NAMES.items():
        if match.url_name in url_names:
            return {"nutrition_active_tab": tab}
    # Onboarding and anything else not in a sub-nav tab (e.g. a future
    # page added here without also being added above) — the sub-nav
    # itself is only shown outside onboarding (_subnav.html's own
    # check), so this mostly matters for "no tab happens to match,
    # don't crash or highlight the wrong one."
    return {"nutrition_active_tab": None}
