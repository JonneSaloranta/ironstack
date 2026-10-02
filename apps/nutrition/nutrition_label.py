"""A food's nutrition table laid out the way an EU/Finnish package label
is (Regulation (EU) 1169/2011 Annex XV): energy in kJ and kcal, fat and
its saturates, carbohydrate and its sugars/starch/polyols, fibre,
protein, salt — then every other nutrient OpenFoodFacts reports.

Pure functions, no DB access: `extract_other_nutrients` turns OFF's raw
`nutriments` dict into `Food.other_nutrients`, `label_rows` turns a Food
into the rows `templates/nutrition/food_detail.html` renders.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

KJ_PER_KCAL = Decimal("4.184")
SALT_PER_SODIUM = Decimal("2.5")

# OFF nutriment keys this app already stores in its own Food columns,
# or that aren't nutrients at all (scores, estimates, footprints).
_HANDLED_KEYS = {
    "fat", "saturated-fat", "carbohydrates", "sugars", "starch", "polyols",
    "fiber", "proteins", "salt", "sodium", "nova-group", "ph",
}
_SKIPPED_PREFIXES = ("energy", "nutrition-score", "fruits-vegetables", "carbon-footprint")

# Display names for the other nutrients OFF commonly reports, in label
# order (fat breakdown, other carbohydrates, vitamins, minerals). Any
# key not listed here still shows, after these, under a name derived
# from the key itself.
OTHER_NUTRIENT_LABELS = {
    "monounsaturated-fat": pgettext_lazy("nutrient", "of which monounsaturates"),
    "polyunsaturated-fat": pgettext_lazy("nutrient", "of which polyunsaturates"),
    "omega-3-fat": pgettext_lazy("nutrient", "Omega-3 fatty acids"),
    "trans-fat": pgettext_lazy("nutrient", "Trans fat"),
    "cholesterol": pgettext_lazy("nutrient", "Cholesterol"),
    "lactose": pgettext_lazy("nutrient", "Lactose"),
    "alcohol": pgettext_lazy("nutrient", "Alcohol"),
    "caffeine": pgettext_lazy("nutrient", "Caffeine"),
    "vitamin-a": pgettext_lazy("nutrient", "Vitamin A"),
    "vitamin-d": pgettext_lazy("nutrient", "Vitamin D"),
    "vitamin-e": pgettext_lazy("nutrient", "Vitamin E"),
    "vitamin-k": pgettext_lazy("nutrient", "Vitamin K"),
    "vitamin-c": pgettext_lazy("nutrient", "Vitamin C"),
    "vitamin-b1": pgettext_lazy("nutrient", "Thiamin (B1)"),
    "vitamin-b2": pgettext_lazy("nutrient", "Riboflavin (B2)"),
    "vitamin-pp": pgettext_lazy("nutrient", "Niacin"),
    "vitamin-b6": pgettext_lazy("nutrient", "Vitamin B6"),
    "vitamin-b9": pgettext_lazy("nutrient", "Folate"),
    "folates": pgettext_lazy("nutrient", "Folate"),
    "vitamin-b12": pgettext_lazy("nutrient", "Vitamin B12"),
    "biotin": pgettext_lazy("nutrient", "Biotin"),
    "pantothenic-acid": pgettext_lazy("nutrient", "Pantothenic acid"),
    "potassium": pgettext_lazy("nutrient", "Potassium"),
    "chloride": pgettext_lazy("nutrient", "Chloride"),
    "calcium": pgettext_lazy("nutrient", "Calcium"),
    "phosphorus": pgettext_lazy("nutrient", "Phosphorus"),
    "magnesium": pgettext_lazy("nutrient", "Magnesium"),
    "iron": pgettext_lazy("nutrient", "Iron"),
    "zinc": pgettext_lazy("nutrient", "Zinc"),
    "copper": pgettext_lazy("nutrient", "Copper"),
    "manganese": pgettext_lazy("nutrient", "Manganese"),
    "fluoride": pgettext_lazy("nutrient", "Fluoride"),
    "selenium": pgettext_lazy("nutrient", "Selenium"),
    "chromium": pgettext_lazy("nutrient", "Chromium"),
    "molybdenum": pgettext_lazy("nutrient", "Molybdenum"),
    "iodine": pgettext_lazy("nutrient", "Iodine"),
}
_LABEL_ORDER = {key: index for index, key in enumerate(OTHER_NUTRIENT_LABELS)}

# OFF stores every mass nutrient's `<key>_100g` in grams; `<key>_unit`
# is the unit the value is meant to be read in.
_FROM_GRAMS = {
    "g": Decimal("1"),
    "mg": Decimal("1000"),
    "µg": Decimal("1000000"),
    "μg": Decimal("1000000"),
    "mcg": Decimal("1000000"),
}


def _normalized(value):
    """A Decimal without float noise or trailing zeros (12.300 → 12.3),
    rounded to 3 decimals — plenty for any label figure."""
    return Decimal(str(value)).quantize(Decimal("0.001")).normalize()


def extract_other_nutrients(nutriments):
    """OFF's raw `nutriments` → `Food.other_nutrients`: every per-100 g/ml
    nutrient not already in a Food column, converted into its own
    display unit, in label order."""
    rows = []
    for full_key, raw_value in nutriments.items():
        if not full_key.endswith("_100g") or raw_value in (None, ""):
            continue
        key = full_key[: -len("_100g")]
        if key in _HANDLED_KEYS or key.startswith(_SKIPPED_PREFIXES) or "prepared" in key:
            continue
        unit = (nutriments.get(f"{key}_unit") or "g").strip()
        try:
            value = Decimal(str(raw_value))
        except ArithmeticError:
            continue
        if unit in _FROM_GRAMS:
            value *= _FROM_GRAMS[unit]
        elif unit not in ("%", "% vol"):
            # IU and other units OFF doesn't convert consistently.
            continue
        rows.append({"key": key, "value": str(_normalized(value)), "unit": unit})
    rows.sort(key=lambda row: (_LABEL_ORDER.get(row["key"], len(_LABEL_ORDER)), row["key"]))
    return rows


@dataclass(frozen=True)
class LabelRow:
    label: str
    value: Decimal | None  # None = not known for this food
    unit: str
    indented: bool = False
    key: str = ""


_FAT_SUB_ROWS = ("monounsaturated-fat", "polyunsaturated-fat")


def energy_kj(food):
    """The label's kJ figure — stored when the source gave one, otherwise
    derived from kcal."""
    if food.energy_kj is not None:
        return food.energy_kj
    return Decimal(food.calories) * KJ_PER_KCAL


def salt_grams(food):
    if food.salt_grams is not None:
        return food.salt_grams
    if food.sodium_mg is not None:
        return Decimal(food.sodium_mg) * SALT_PER_SODIUM / 1000
    return None


def label_rows(food):
    """The food's nutrients in label order, energy excluded (it has its
    own kJ/kcal row). The mandatory rows always show — `value=None` for
    an unknown one — while starch/polyols/fibre only show when known,
    the same way a printed label lists them."""
    def sub(label, value):
        return LabelRow(label, value, "g", indented=True)

    rows = [
        LabelRow(_("Fat"), food.fat_grams, "g"),
        sub(pgettext_lazy("nutrient", "of which saturates"), food.saturated_fat_grams),
        # Fat's other sub-rows, when OFF had them, belong right here
        # rather than down among the vitamins.
        *(row for row in _other_rows(food) if row.key in _FAT_SUB_ROWS),
        LabelRow(_("Carbohydrates"), food.carbohydrate_grams, "g"),
        sub(pgettext_lazy("nutrient", "of which sugars"), food.sugar_grams),
    ]
    if food.starch_grams is not None:
        rows.append(sub(pgettext_lazy("nutrient", "of which starch"), food.starch_grams))
    if food.polyols_grams is not None:
        rows.append(sub(pgettext_lazy("nutrient", "of which polyols"), food.polyols_grams))
    if food.fiber_grams is not None:
        rows.append(LabelRow(pgettext_lazy("nutrient", "Fibre"), food.fiber_grams, "g"))
    rows.append(LabelRow(_("Protein"), food.protein_grams, "g"))
    rows.append(LabelRow(pgettext_lazy("nutrient", "Salt"), salt_grams(food), "g"))
    return rows


def _other_rows(food):
    for item in food.other_nutrients or []:
        key = item.get("key", "")
        label = OTHER_NUTRIENT_LABELS.get(key) or key.replace("-", " ").capitalize()
        yield LabelRow(
            label, Decimal(item["value"]), item.get("unit", "g"),
            indented=key in _FAT_SUB_ROWS, key=key,
        )


def other_rows(food):
    """`Food.other_nutrients` as label rows, after the main table (minus
    the fat sub-rows `label_rows` already placed under fat)."""
    return [row for row in _other_rows(food) if row.key not in _FAT_SUB_ROWS]
