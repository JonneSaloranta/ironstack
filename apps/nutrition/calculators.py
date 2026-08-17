"""Standalone, stateless calculators — see docs/NUTRITION.md
"Calculators": quick, useful numbers a user can check without
onboarding, setting a goal, or logging anything. Nothing here reads or
writes a single nutrition domain row; every function takes plain
numbers and returns a plain number (or a small frozen dataclass), the
same pure-function shape as apps.nutrition.energy/macros — the two
BMR/TDEE and macro-split calculators are thin wrappers around exactly
those two modules rather than a second implementation of the same
math (see CLAUDE.md "do not create duplicate abstractions").

Every result here is an estimate, not a diagnosis or medical advice —
same framing apps.nutrition.energy's own docstring insists on
throughout this app.
"""

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils.translation import gettext_lazy as _

from .models import ActivityLevel, BiologicalSex

BODY_FAT_PLACES = Decimal("0.1")
WATER_LITER_PLACES = Decimal("0.1")


def estimate_body_fat_percent(
    *,
    biological_sex: str,
    height_cm: Decimal,
    neck_cm: Decimal,
    waist_cm: Decimal,
    hip_cm: Decimal | None = None,
) -> Decimal | None:
    """The U.S. Navy circumference method — chosen over skinfold
    calipers (needs a tool almost nobody owns) or bioimpedance scales
    (notoriously inconsistent between devices and even between two
    readings on the same device): just a tape measure and three (men)
    or four (women) circumferences. Validation studies against DEXA
    put it within roughly 3-4 percentage points for most body types —
    presented as an estimate, not a diagnosis.

    Returns `None` for physically nonsensical input (a waist not
    bigger than the neck for men; waist+hip not bigger than the neck
    for women) rather than raising or silently returning a nonsense
    negative percentage — the caller shows this as "check your
    measurements" rather than a crash.
    """
    if biological_sex == BiologicalSex.MALE:
        diff = waist_cm - neck_cm
        if diff <= 0 or height_cm <= 0:
            return None
        value = (
            495
            / (
                Decimal("1.0324")
                - Decimal("0.19077") * Decimal(math.log10(float(diff)))
                + Decimal("0.15456") * Decimal(math.log10(float(height_cm)))
            )
            - 450
        )
    else:
        if hip_cm is None:
            return None
        diff = waist_cm + hip_cm - neck_cm
        if diff <= 0 or height_cm <= 0:
            return None
        value = (
            495
            / (
                Decimal("1.29579")
                - Decimal("0.35004") * Decimal(math.log10(float(diff)))
                + Decimal("0.22100") * Decimal(math.log10(float(height_cm)))
            )
            - 450
        )
    if value <= 0:
        return None
    return value.quantize(BODY_FAT_PLACES, rounding=ROUND_HALF_UP)


# A common rule-of-thumb baseline (roughly 33 ml per kg bodyweight a
# day), plus a flat bonus for how active the day typically is. Reuses
# the same five ActivityLevel buckets as
# apps.nutrition.energy.ACTIVITY_MULTIPLIERS rather than a second,
# differently-defined activity scale, so a user who's already set an
# activity level elsewhere (onboarding, the BMR/TDEE calculator) sees
# one consistent picture of what their own activity level means
# throughout the app.
WATER_BASE_ML_PER_KG = Decimal("33")
WATER_ACTIVITY_BONUS_ML = {
    ActivityLevel.SEDENTARY: Decimal("0"),
    ActivityLevel.LIGHT: Decimal("250"),
    ActivityLevel.MODERATE: Decimal("500"),
    ActivityLevel.ACTIVE: Decimal("750"),
    ActivityLevel.VERY_ACTIVE: Decimal("1000"),
}


def estimate_daily_water_liters(weight_kg: Decimal, activity_level: str) -> Decimal:
    """Deliberately rough: real fluid needs vary with climate, sweat
    rate, and diet composition well beyond what bodyweight and an
    activity bucket can capture — presented as a starting point, not a
    target to hit exactly, same "estimate" framing as everywhere else
    in this app."""
    base_ml = weight_kg * WATER_BASE_ML_PER_KG
    total_ml = base_ml + WATER_ACTIVITY_BONUS_ML[activity_level]
    return (total_ml / Decimal("1000")).quantize(WATER_LITER_PLACES, rounding=ROUND_HALF_UP)


WHR_PLACES = Decimal("0.01")


def calculate_waist_hip_ratio(waist_cm: Decimal, hip_cm: Decimal) -> Decimal | None:
    """`None` for a non-positive hip measurement (can't divide by zero,
    and it's nonsensical anyway) rather than raising."""
    if waist_cm is None or hip_cm is None or hip_cm <= 0:
        return None
    return (waist_cm / hip_cm).quantize(WHR_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class WHRRiskLevel:
    name: str
    low: Decimal | None  # inclusive
    high: Decimal | None  # exclusive


# WHO-style sex-specific thresholds — a widely cited public-health
# screening cutoff for cardiovascular/metabolic risk associated with
# where fat is carried, same "published scale, not invented here"
# reasoning as apps.nutrition.models.NutriScoreGrade. Two separate
# tables (not one adjusted by a correction factor) since the
# thresholds themselves, not just typical values, differ by sex.
WHR_RISK_LEVELS = {
    BiologicalSex.MALE: [
        WHRRiskLevel(_("Lower risk"), None, Decimal("0.90")),
        WHRRiskLevel(_("Moderate risk"), Decimal("0.90"), Decimal("1.0")),
        WHRRiskLevel(_("Higher risk"), Decimal("1.0"), None),
    ],
    BiologicalSex.FEMALE: [
        WHRRiskLevel(_("Lower risk"), None, Decimal("0.80")),
        WHRRiskLevel(_("Moderate risk"), Decimal("0.80"), Decimal("0.85")),
        WHRRiskLevel(_("Higher risk"), Decimal("0.85"), None),
    ],
}


def whr_risk_level(ratio: Decimal, biological_sex: str) -> WHRRiskLevel | None:
    for level in WHR_RISK_LEVELS[biological_sex]:
        if (level.low is None or ratio >= level.low) and (
            level.high is None or ratio < level.high
        ):
            return level
    return None


def estimate_weeks_to_goal(
    current_weight_kg: Decimal, target_weight_kg: Decimal, rate_kg_per_week: Decimal
) -> Decimal | None:
    """How many weeks, at a chosen rate, to go from `current_weight_kg`
    to `target_weight_kg` — the same signed-rate convention
    `NutritionGoal.target_rate_kg_per_week` already uses (negative for
    losing, positive for gaining). `None` when the rate doesn't
    actually move toward the target (a positive/gaining rate with a
    lower target, a negative/losing rate with a higher target, or a
    zero rate with any real gap) rather than a nonsensical negative or
    infinite week count — the caller shows this as "check your
    numbers" rather than a silently wrong answer. Already-at-target
    returns zero, not `None` — that's a real, correct answer, not an
    error. Deliberately returns only a week count, not a calendar
    date — "today" is a request-time concern the caller already has
    (`timezone.localdate()`), not something a pure calculation needs
    to reach for on its own, the same "no DB/HTTP dependency" rule
    this whole module follows."""
    gap = target_weight_kg - current_weight_kg
    if gap == 0:
        return Decimal("0")
    if rate_kg_per_week == 0 or (gap > 0) != (rate_kg_per_week > 0):
        return None
    return (gap / rate_kg_per_week).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
