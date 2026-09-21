"""A thin, on-demand Open Prices client — the crowdsourced pricing
sibling to apps.nutrition.openfoodfacts (docs/NUTRITION.md "Open
Prices integration"). Open Prices (prices.openfoodfacts.org) is a
distinct OpenFoodFacts project from the core product API that module
talks to: OFF's own product data has no price field at all, and Open
Prices' price reports are shopper-submitted, per-store, per-day, and
per-currency, with no single "the price" for a product. Same
on-demand, no-bulk-sync shape as openfoodfacts.py — see that module's
own docstring for why.
"""

from decimal import Decimal
from statistics import median

from . import off_http

API_BASE = "https://prices.openfoodfacts.org/api/v1"
# Requests go through apps.nutrition.off_http: same identifying
# User-Agent and per-minute budget as OFF's core API — a distinct
# project, but run by the same organization with the same expectations.

# How many of the most recent price reports to pull per product —
# enough to smooth over a handful of outlier or misread entries
# without pulling a popular product's entire multi-year price history
# just to show one "what does this typically cost" figure.
PRICE_SAMPLE_SIZE = 20


class OpenPricesError(Exception):
    """Raised for a network/parse failure — never for "no price
    reports exist yet for this product," which is a normal, silently
    empty outcome, not an error."""


def get_prices_for_barcode(barcode, *, size=PRICE_SAMPLE_SIZE):
    """The most recent raw Open Prices price reports for one product
    barcode (unparsed; call `summarize_prices` to collapse them into
    one figure). Newest first, so a product whose price has changed
    over the years is summarized from current reports, not old ones.
    """
    try:
        response = off_http.get(
            f"{API_BASE}/prices",
            bucket="read",
            params={
                "product_code": barcode,
                "order_by": "-date",
                "size": size,
            },
        )
        return response.json().get("items", [])
    except (off_http.OffRequestError, ValueError) as exc:
        raise OpenPricesError(str(exc)) from exc


def summarize_prices(raw_prices):
    """Collapses a list of individual, per-store price reports into
    one representative figure. Open Prices has no concept of "the"
    price for a product — every report is tied to one shopper, one
    store, one day, in whatever currency that store trades in — so
    this groups reports by currency, keeps whichever currency has the
    most reports in the sample (the most representative one for
    wherever most of this product's reports come from), and returns
    that currency's median price alongside how many reports it's
    based on. Returns `None` if `raw_prices` is empty or none of its
    entries have both a price and a currency."""
    by_currency = {}
    for entry in raw_prices:
        price = entry.get("price")
        currency = entry.get("currency")
        if price is None or not currency:
            continue
        by_currency.setdefault(currency, []).append(Decimal(str(price)))
    if not by_currency:
        return None
    currency, amounts = max(by_currency.items(), key=lambda pair: len(pair[1]))
    return {
        "amount": median(amounts).quantize(Decimal("0.01")),
        "currency": currency,
        "sample_count": len(amounts),
    }
