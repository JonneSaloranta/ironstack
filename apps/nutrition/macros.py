"""Macro (protein/carbohydrate/fat) target calculation — see
docs/NUTRITION.md "Macros". Pure functions, same shape as
apps.nutrition.energy: no Django DB/HTTP dependency.

Not locked to one algorithm (spec requirement): every default here is
a keyword argument on `calculate_macros`, so a future preset (e.g.
"high-carb," "keto") is a new caller with different overrides, not a
rewrite of this module.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .models import GoalType

GRAM_PLACES = Decimal("0.01")
PERCENT_PLACES = Decimal("0.1")

KCAL_PER_G_PROTEIN = Decimal("4")
KCAL_PER_G_CARBOHYDRATE = Decimal("4")
KCAL_PER_G_FAT = Decimal("9")

# g of protein per kg bodyweight, by goal — higher in a deficit to
# spare lean mass, matching the sports-nutrition consensus this
# project's other domain modules (docs/PROGRESSION.md) also lean on
# for defaults rather than inventing numbers from nothing.
DEFAULT_PROTEIN_G_PER_KG = {
    GoalType.FAT_LOSS_AGGRESSIVE: Decimal("2.2"),
    GoalType.FAT_LOSS_MODERATE: Decimal("2.2"),
    GoalType.FAT_LOSS_CONSERVATIVE: Decimal("2.2"),
    GoalType.MAINTENANCE: Decimal("1.8"),
    GoalType.MUSCLE_GAIN_LEAN: Decimal("2.0"),
    GoalType.MUSCLE_GAIN_MODERATE: Decimal("2.0"),
    GoalType.MUSCLE_GAIN_AGGRESSIVE: Decimal("2.0"),
}
DEFAULT_FAT_PERCENT = Decimal("0.25")


@dataclass(frozen=True)
class MacroBreakdown:
    protein_grams: Decimal
    carbohydrate_grams: Decimal
    fat_grams: Decimal
    protein_kcal: Decimal
    carbohydrate_kcal: Decimal
    fat_kcal: Decimal
    protein_percent: Decimal
    carbohydrate_percent: Decimal
    fat_percent: Decimal
    fat_was_reduced: bool
    protein_was_reduced: bool


def calculate_macros(
    weight_kg: Decimal,
    daily_calories: int,
    goal_type: str,
    *,
    protein_g_per_kg: Decimal | None = None,
    fat_percent: Decimal | None = None,
) -> MacroBreakdown:
    """Protein and fat are set first (by g/kg bodyweight and % of
    calories respectively), carbohydrate takes whatever's left. If
    protein alone, or protein+fat together, would exceed
    `daily_calories` (only realistic at an unusually low calorie
    target with high protein), fat is reduced first, then — only if
    that still isn't enough — protein itself, rather than ever letting
    carbohydrate go negative. Both reductions are reported so the
    caller can tell the user what happened, the same transparency
    `apps.nutrition.energy.calculate_calorie_target` gives its own
    clamps."""
    daily_calories = Decimal(daily_calories)
    protein_g_per_kg = protein_g_per_kg or DEFAULT_PROTEIN_G_PER_KG[goal_type]
    fat_percent = DEFAULT_FAT_PERCENT if fat_percent is None else fat_percent

    protein_kcal = weight_kg * protein_g_per_kg * KCAL_PER_G_PROTEIN
    fat_kcal = daily_calories * fat_percent

    protein_was_reduced = False
    fat_was_reduced = False

    if protein_kcal > daily_calories:
        protein_kcal = daily_calories
        protein_was_reduced = True
        fat_kcal = Decimal("0")
        fat_was_reduced = True
    elif protein_kcal + fat_kcal > daily_calories:
        fat_kcal = daily_calories - protein_kcal
        fat_was_reduced = True

    carbohydrate_kcal = max(daily_calories - protein_kcal - fat_kcal, Decimal("0"))

    protein_grams = (protein_kcal / KCAL_PER_G_PROTEIN).quantize(
        GRAM_PLACES, rounding=ROUND_HALF_UP
    )
    fat_grams = (fat_kcal / KCAL_PER_G_FAT).quantize(GRAM_PLACES, rounding=ROUND_HALF_UP)
    carbohydrate_grams = (carbohydrate_kcal / KCAL_PER_G_CARBOHYDRATE).quantize(
        GRAM_PLACES, rounding=ROUND_HALF_UP
    )

    # Recompute kcal from the *quantized* grams, so a displayed
    # "180 g protein" and its displayed kcal figure always agree —
    # rounding grams first then deriving kcal from that, not the other
    # way around.
    protein_kcal = protein_grams * KCAL_PER_G_PROTEIN
    fat_kcal = fat_grams * KCAL_PER_G_FAT
    carbohydrate_kcal = carbohydrate_grams * KCAL_PER_G_CARBOHYDRATE

    def _percent(kcal: Decimal) -> Decimal:
        if daily_calories <= 0:
            return Decimal("0")
        return (kcal / daily_calories * 100).quantize(PERCENT_PLACES, rounding=ROUND_HALF_UP)

    return MacroBreakdown(
        protein_grams=protein_grams,
        carbohydrate_grams=carbohydrate_grams,
        fat_grams=fat_grams,
        protein_kcal=protein_kcal,
        carbohydrate_kcal=carbohydrate_kcal,
        fat_kcal=fat_kcal,
        protein_percent=_percent(protein_kcal),
        carbohydrate_percent=_percent(carbohydrate_kcal),
        fat_percent=_percent(fat_kcal),
        fat_was_reduced=fat_was_reduced,
        protein_was_reduced=protein_was_reduced,
    )
