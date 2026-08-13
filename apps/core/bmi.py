"""BMI (body mass index): weight in kg over height in meters, squared.

Standard WHO adult category thresholds — this is a widely-used screening
number, not a diagnosis; the UI only ever shows it alongside the plain
category ranges (and, once a height is known, the equivalent weight
range for each) so a user can see where they land, never as advice.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils.translation import gettext_lazy as _

from . import units as core_units

BMI_PLACES = Decimal("0.1")
WEIGHT_PLACES = Decimal("0.1")


@dataclass(frozen=True)
class BMICategory:
    name: str
    low: Decimal | None  # inclusive
    high: Decimal | None  # exclusive


# Built once at import time, so these must stay lazy translations
# (gettext_lazy) — an eager gettext() call here would freeze every
# category's name into whatever language happened to be active during
# process startup, never re-translating per-request afterward.
BMI_CATEGORIES = [
    BMICategory(_("Underweight"), None, Decimal("18.5")),
    BMICategory(_("Normal weight"), Decimal("18.5"), Decimal("25")),
    BMICategory(_("Overweight"), Decimal("25"), Decimal("30")),
    BMICategory(_("Obese"), Decimal("30"), None),
]


def calculate_bmi(weight_kg: Decimal, height_m: Decimal) -> Decimal | None:
    """`None` if either input is missing or height is non-positive (can't
    divide by zero, and a non-positive height is nonsensical anyway)."""
    if weight_kg is None or height_m is None or height_m <= 0:
        return None
    bmi = weight_kg / (height_m * height_m)
    return bmi.quantize(BMI_PLACES, rounding=ROUND_HALF_UP)


def category_for(bmi: Decimal) -> BMICategory | None:
    for category in BMI_CATEGORIES:
        if (category.low is None or bmi >= category.low) and (
            category.high is None or bmi < category.high
        ):
            return category
    return None


@dataclass(frozen=True)
class CategoryRow:
    """One row of the category ranges table: the BMI bounds themselves,
    plus (once a height is known) the weight, in the user's preferred
    unit, those same bounds correspond to — "what does 'Normal weight'
    actually mean for *my* height" is the useful question a bare BMI
    number range doesn't answer on its own."""

    category: BMICategory
    weight_low: Decimal | None
    weight_high: Decimal | None


def _weight_for_bmi(bmi: Decimal, height_m: Decimal) -> Decimal:
    return bmi * height_m * height_m


def category_rows(height_m: Decimal | None, unit_system: str) -> list[CategoryRow]:
    """`BMI_CATEGORIES`, each paired with its equivalent weight range at
    `height_m` (in `unit_system`'s display unit) — `weight_low`/
    `weight_high` are both `None` on every row when `height_m` is falsy,
    so the table degrades gracefully to BMI-only ranges without a height
    on file, same as `calculate_bmi` itself does."""
    rows = []
    for category in BMI_CATEGORIES:
        weight_low = weight_high = None
        if height_m:
            if category.low is not None:
                weight_low = core_units.kg_to_display(
                    _weight_for_bmi(category.low, height_m), unit_system
                ).quantize(WEIGHT_PLACES, rounding=ROUND_HALF_UP)
            if category.high is not None:
                weight_high = core_units.kg_to_display(
                    _weight_for_bmi(category.high, height_m), unit_system
                ).quantize(WEIGHT_PLACES, rounding=ROUND_HALF_UP)
        rows.append(CategoryRow(category=category, weight_low=weight_low, weight_high=weight_high))
    return rows
