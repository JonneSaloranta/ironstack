"""Estimated one-rep-max calculation.

Kept behind its own small interface, not hard-coded into PR detection, per
docs/PR_SYSTEM.md: "Do not hard-code the application to one formula. Put
the calculation behind a service/interface so the formula can be changed
later." Swap `DEFAULT_FORMULA`, or pass `formula=` explicitly, to change
which one is used — no caller outside this module needs to change.
"""

from decimal import Decimal

TWO_PLACES = Decimal("0.01")


def _epley(weight: Decimal, reps: int) -> Decimal:
    return weight * (Decimal(1) + Decimal(reps) / Decimal(30))


def _brzycki(weight: Decimal, reps: int) -> Decimal:
    # Denominator hits zero at reps=37 and goes negative beyond that;
    # the formula is only meant for realistic rep ranges (roughly 1-12).
    return weight * Decimal(36) / (Decimal(37) - Decimal(reps))


_FORMULAS = {
    "epley": _epley,
    "brzycki": _brzycki,
}

DEFAULT_FORMULA = "epley"


class OneRepMaxCalculator:
    """Estimates a one-rep max from a single (weight, reps) set."""

    def __init__(self, formula: str = DEFAULT_FORMULA):
        if formula not in _FORMULAS:
            raise ValueError(f"Unknown 1RM formula: {formula!r}")
        self.formula = formula

    def estimate(self, weight: Decimal, reps: int) -> Decimal:
        if reps < 1:
            raise ValueError("Estimated 1RM requires at least 1 rep.")
        return _FORMULAS[self.formula](weight, reps).quantize(TWO_PLACES)
