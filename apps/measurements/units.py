"""Display-unit conversion for body measurements.

Dispatches by `MeasurementType.unit_kind` + the user's `unit_system`
preference (apps.accounts) onto apps.core.units' generic conversion
primitives — this module only decides *which* conversion applies,
matching apps.exercises/apps.programs' pattern of keeping unit-aware
display logic out of templates.
"""

from decimal import Decimal

from apps.accounts.models import UnitSystem
from apps.core import units as core_units

from .models import UnitKind

_UNIT_LABELS = {
    (UnitKind.WEIGHT, UnitSystem.METRIC): "kg",
    (UnitKind.WEIGHT, UnitSystem.IMPERIAL): "lb",
    (UnitKind.LENGTH, UnitSystem.METRIC): "cm",
    (UnitKind.LENGTH, UnitSystem.IMPERIAL): "in",
    (UnitKind.PERCENTAGE, UnitSystem.METRIC): "%",
    (UnitKind.PERCENTAGE, UnitSystem.IMPERIAL): "%",
}


def display_unit_label(unit_kind: str, unit_system: str) -> str:
    return _UNIT_LABELS.get((unit_kind, unit_system), "")


def to_display(value: Decimal, unit_kind: str, unit_system: str) -> Decimal:
    """Canonical stored value -> the user's preferred display unit.

    Canonical storage keeps more precision than anyone needs to look at
    (0.1mm for lengths, so cm/inch round-trips never lose data — see
    apps.core.units) — quantized here to what's actually worth displaying,
    independent of the (unrelated) precision decision for storage.
    """
    if unit_kind == UnitKind.WEIGHT:
        raw = value if unit_system == UnitSystem.METRIC else core_units.kg_to_lb(value)
        return raw.quantize(Decimal("0.01"))
    if unit_kind == UnitKind.LENGTH:
        raw = (
            core_units.meters_to_cm(value)
            if unit_system == UnitSystem.METRIC
            else core_units.meters_to_inches(value)
        )
        return raw.quantize(Decimal("0.1"))
    return value.quantize(Decimal("0.01"))  # percentage: dimensionless, no unit conversion


def to_canonical(value: Decimal, unit_kind: str, unit_system: str) -> Decimal:
    """A value entered in the user's preferred unit -> canonical storage."""
    if unit_kind == UnitKind.WEIGHT:
        return value if unit_system == UnitSystem.METRIC else core_units.lb_to_kg(value)
    if unit_kind == UnitKind.LENGTH:
        return (
            core_units.cm_to_meters(value)
            if unit_system == UnitSystem.METRIC
            else core_units.inches_to_meters(value)
        )
    return value
