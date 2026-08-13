"""Generic chart data prep, shared by any app that needs to plot values —
kept out of views/templates per CLAUDE.md ("do not put analytics logic in
templates"). `build_chart_series` (line, change-over-time) was promoted
here from apps.measurements once apps.activities needed the same thing;
`build_bar_series` (bar, category comparison) was added directly here in
Phase 10 for apps.analytics, for the same reason — one shared,
model-agnostic implementation instead of each app rolling its own.

Both take plain tuples rather than model instances, so neither has an
opinion about field names — callers project whatever they're plotting
into that shape first.
"""

from dataclasses import dataclass
from decimal import Decimal

TWO_PLACES = Decimal("0.01")
MAX_BAR_THICKNESS = Decimal("24")  # dataviz mark spec: bars capped, never fill the slot


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


@dataclass(frozen=True)
class BarPoint:
    label: str
    value: Decimal
    x: Decimal
    y: Decimal
    width: Decimal
    height: Decimal


@dataclass(frozen=True)
class BarSeries:
    bars: list
    max_value: Decimal
    width: int
    height: int


def build_bar_series(categories, *, width=600, height=200, padding=20):
    """`categories`: an iterable of `(label, value)` pairs, in display
    order (not sorted here — callers decide chronological vs.
    ranked-by-value). Returns None for no categories at all; unlike line
    charts, a single bar is still a meaningful comparison-of-one.
    """
    categories = list(categories)
    if not categories:
        return None

    values = [value for _, value in categories]
    max_value = max(values) or Decimal("1")  # avoid /0 when every value is zero
    plot_width = Decimal(width - 2 * padding)
    plot_height = Decimal(height - 2 * padding)
    baseline = Decimal(height - padding)
    slot_width = plot_width / Decimal(len(categories))
    bar_width = min(slot_width * Decimal("0.6"), MAX_BAR_THICKNESS)

    bars = []
    for i, (label, value) in enumerate(categories):
        slot_x = Decimal(padding) + Decimal(i) * slot_width
        bar_x = slot_x + (slot_width - bar_width) / 2
        bar_height = (value / max_value) * plot_height if max_value else Decimal("0")
        bars.append(
            BarPoint(
                label=label,
                value=value,
                x=bar_x.quantize(TWO_PLACES),
                y=(baseline - bar_height).quantize(TWO_PLACES),
                width=bar_width.quantize(TWO_PLACES),
                height=bar_height.quantize(TWO_PLACES),
            )
        )

    return BarSeries(bars=bars, max_value=max_value, width=width, height=height)
