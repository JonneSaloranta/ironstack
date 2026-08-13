"""BMI (body mass index): weight in kg over height in meters, squared.

Standard WHO adult category thresholds — this is a widely-used screening
number, not a diagnosis; the UI only ever shows it alongside the plain
category ranges so a user can see where they land, never as advice.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils.translation import gettext_lazy as _

BMI_PLACES = Decimal("0.1")


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
