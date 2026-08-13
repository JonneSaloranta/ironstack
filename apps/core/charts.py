"""Generic single-series line chart data prep, shared by any app that
needs to plot a value trend over time (currently apps.measurements and
apps.activities) — kept out of views/templates per CLAUDE.md ("do not put
analytics logic in templates"). Promoted here from apps.measurements once
apps.activities needed the same thing, rather than duplicating it or
having one domain app import from an unrelated sibling.

Takes plain `(value, date)` tuples rather than model instances, so it has
no opinion about field names — callers project whatever they're plotting
into that shape first.
"""

from dataclasses import dataclass
from decimal import Decimal

TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class ChartPoint:
    x: Decimal
    y: Decimal
    value: Decimal
    date: object


@dataclass(frozen=True)
class ChartSeries:
    points: list
    polyline: str
    min_value: Decimal
    max_value: Decimal
    width: int
    height: int


def build_chart_series(readings, *, width=600, height=200, padding=20):
    """`readings`: an iterable of `(value, date)` tuples, any order.

    Normalizes into SVG-ready coordinates, oldest first. Returns None for
    fewer than 2 points — a single dot isn't a trend line.
    """
    ordered = sorted(readings, key=lambda reading: reading[1])
    if len(ordered) < 2:
        return None

    values = [value for value, _ in ordered]
    min_value, max_value = min(values), max(values)
    span = max_value - min_value or Decimal("1")  # avoid /0 when every value is equal
    plot_width = Decimal(width - 2 * padding)
    plot_height = Decimal(height - 2 * padding)
    last_index = len(ordered) - 1

    points = []
    for i, (value, date) in enumerate(ordered):
        x = Decimal(padding) + (Decimal(i) / Decimal(last_index)) * plot_width
        y = Decimal(height - padding) - ((value - min_value) / span) * plot_height
        points.append(
            ChartPoint(x=x.quantize(TWO_PLACES), y=y.quantize(TWO_PLACES), value=value, date=date)
        )

    return ChartSeries(
        points=points,
        polyline=" ".join(f"{p.x},{p.y}" for p in points),
        min_value=min_value,
        max_value=max_value,
        width=width,
        height=height,
    )
