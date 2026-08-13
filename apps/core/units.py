"""Unit conversion helpers.

Canonical storage units are kilograms (weight) and meters (distance) — see
docs/ARCHITECTURE.md "Units and precision". These pure functions convert to
and from a user's preferred display unit at the template/service boundary.
Values are Decimal in, Decimal out to avoid float rounding drift.
"""

from decimal import ROUND_HALF_UP, Decimal

_KG_PER_LB = Decimal("0.45359237")
_M_PER_MILE = Decimal("1609.344")
_M_PER_INCH = Decimal("0.0254")

# Body circumferences (waist, chest, ...) live at the centimeter scale, so
# converting through the meters canonical unit at only 2 decimal places
# (adequate for kg/lb) would round away real precision — e.g. an 85.5cm
# waist. Length conversions round to 4 places (0.1mm) instead: far finer
# than any tape measure, but enough that no realistic cm/inch input is
# ever rounded on the way in.
_LENGTH_PLACES = "0.0001"


def _quantize(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def kg_to_lb(kg: Decimal) -> Decimal:
    return _quantize(kg / _KG_PER_LB)


def lb_to_kg(lb: Decimal) -> Decimal:
    return _quantize(lb * _KG_PER_LB)


def meters_to_miles(meters: Decimal) -> Decimal:
    return _quantize(meters / _M_PER_MILE)


def miles_to_meters(miles: Decimal) -> Decimal:
    return _quantize(miles * _M_PER_MILE)


def meters_to_km(meters: Decimal) -> Decimal:
    return _quantize(meters / Decimal("1000"))


def km_to_meters(km: Decimal) -> Decimal:
    return _quantize(km * Decimal("1000"))


def meters_to_cm(meters: Decimal) -> Decimal:
    return _quantize(meters * Decimal("100"), _LENGTH_PLACES)


def cm_to_meters(cm: Decimal) -> Decimal:
    return _quantize(cm / Decimal("100"), _LENGTH_PLACES)


def meters_to_inches(meters: Decimal) -> Decimal:
    return _quantize(meters / _M_PER_INCH, _LENGTH_PLACES)


def inches_to_meters(inches: Decimal) -> Decimal:
    return _quantize(inches * _M_PER_INCH, _LENGTH_PLACES)


# Weight is stored in kg everywhere outside apps.measurements (workout
# sets, prescriptions, PRs, analytics) — these three helpers are the
# single dispatch point for converting it to/from a user's preferred
# `unit_system` ("metric"/"imperial"), so every one of those call sites
# converts the same way instead of each re-implementing the branch.
# (apps.measurements.units has its own broader to_display/to_canonical
# dispatch across weight/length/percentage kinds — this is the same idea,
# just scoped to the one kind everything outside that app needs.)
def kg_to_display(kg: Decimal, unit_system: str) -> Decimal:
    return kg_to_lb(kg) if unit_system == "imperial" else kg


def display_to_kg(value: Decimal, unit_system: str) -> Decimal:
    return lb_to_kg(value) if unit_system == "imperial" else value


def weight_unit_label(unit_system: str) -> str:
    return "lb" if unit_system == "imperial" else "kg"
