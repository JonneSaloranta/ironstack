"""Display-unit conversion for activity distance.

Only one field needs it (distance — canonical meters, see
docs/ARCHITECTURE.md), so unlike apps.measurements.units this has no
unit_kind to dispatch on: just metric (km) vs imperial (miles), reusing
apps.core.units' existing conversions (already built for workout/activity
distances, unused until now).
"""

from decimal import Decimal

from apps.accounts.models import UnitSystem
from apps.core import units as core_units


def distance_unit_label(unit_system: str) -> str:
    return "km" if unit_system == UnitSystem.METRIC else "mi"


def distance_to_display(meters: Decimal, unit_system: str) -> Decimal:
    if meters is None:
        return None
    return (
        core_units.meters_to_km(meters)
        if unit_system == UnitSystem.METRIC
        else core_units.meters_to_miles(meters)
    )


def distance_to_canonical(value: Decimal, unit_system: str) -> Decimal:
    if value is None:
        return None
    return (
        core_units.km_to_meters(value)
        if unit_system == UnitSystem.METRIC
        else core_units.miles_to_meters(value)
    )
