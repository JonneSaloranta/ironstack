"""A thin, on-demand OpenFoodFacts client — see docs/NUTRITION.md
"OpenFoodFacts integration" for why this is per-lookup, not a bulk
dataset import/sync.

Deliberately narrow: two read-only functions against OFF's public JSON
API, and a parser mapping their product shape onto this app's own
`Food` fields. No writes, no auth — apps.nutrition.services is where a
parsed result actually becomes (or updates) a `Food` row.
"""

from decimal import Decimal

import requests

from apps.core.version import get_version

API_BASE = "https://world.openfoodfacts.org"
REQUEST_TIMEOUT_SECONDS = 10
# OpenFoodFacts' API rejects requests with no/generic User-Agent (a
# bare "python-requests/x.y" gets a 403) — their own documented usage
# policy asks for an app name, version, and a way to reach the
# operator. Discovered live: a mocked-request test wouldn't have
# caught this at all, since the mock never touches this header.
USER_AGENT = f"IronStack/{get_version()} (self-hosted fitness tracker)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT}
# kcal per 100g/100ml is OFF's own universal unit for every product,
# regardless of that product's real-world serving size — matches this
# app's own "per serving_size of serving_unit" Food shape directly
# when serving_size=100.
PER_100_SERVING_SIZE = Decimal("100")


class OpenFoodFactsError(Exception):
    """Raised for a network/parse failure — never for "no results,"
    which is a normal, silently-empty outcome, not an error."""


def search_products(query, *, page_size=20):
    """Free-text search — returns a list of raw OFF product dicts
    (unparsed; call `parse_product` on each to get this app's shape).
    """
    try:
        response = requests.get(
            f"{API_BASE}/cgi/search.pl",
            params={
                "search_terms": query,
                "search_simple": 1,
                "action": "process",
                "json": 1,
                "page_size": page_size,
            },
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json().get("products", [])
    except (requests.RequestException, ValueError) as exc:
        raise OpenFoodFactsError(str(exc)) from exc


def get_product(barcode):
    """A single raw OFF product dict by barcode, or `None` if OFF has
    no such product (a normal outcome, not an error)."""
    try:
        response = requests.get(
            f"{API_BASE}/api/v2/product/{barcode}.json",
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise OpenFoodFactsError(str(exc)) from exc
    if payload.get("status") != 1:
        return None
    return payload.get("product")


def parse_product(raw):
    """Maps a raw OFF product dict onto this app's `Food` field names,
    always as "per 100g/100ml" (OFF's own universal unit) regardless
    of the product's real package size. Returns `None` if the product
    is missing its barcode or core macros entirely — a product OFF
    itself has incomplete data for isn't worth creating a Food row
    that would show misleading zeros."""
    barcode = raw.get("code")
    nutriments = raw.get("nutriments") or {}
    calories = nutriments.get("energy-kcal_100g")
    protein = nutriments.get("proteins_100g")
    carbohydrate = nutriments.get("carbohydrates_100g")
    fat = nutriments.get("fat_100g")
    if not barcode or None in (calories, protein, carbohydrate, fat):
        return None

    name = raw.get("product_name") or raw.get("generic_name")
    if not name:
        return None

    def _optional(key):
        value = nutriments.get(key)
        return Decimal(str(value)) if value is not None else None

    return {
        "off_id": barcode,
        "name": name,
        "brand": (raw.get("brands") or "").split(",")[0].strip(),
        "serving_size": PER_100_SERVING_SIZE,
        "serving_unit": "ml" if raw.get("product_quantity_unit") == "ml" else "g",
        "calories": int(round(float(calories))),
        "protein_grams": Decimal(str(protein)),
        "carbohydrate_grams": Decimal(str(carbohydrate)),
        "fat_grams": Decimal(str(fat)),
        "fiber_grams": _optional("fiber_100g"),
        "sugar_grams": _optional("sugars_100g"),
        "saturated_fat_grams": _optional("saturated-fat_100g"),
        "sodium_mg": (
            int(round(float(nutriments["sodium_100g"]) * 1000))
            if nutriments.get("sodium_100g") is not None
            else None
        ),
    }
