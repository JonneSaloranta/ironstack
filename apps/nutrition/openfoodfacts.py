"""A thin, on-demand OpenFoodFacts client — see docs/NUTRITION.md
"OpenFoodFacts integration" for why this is per-lookup, not a bulk
dataset import/sync.

Deliberately narrow: two read-only functions against OFF's public JSON
API, and a parser mapping their product shape onto this app's own
`Food` fields. No writes, no auth — apps.nutrition.services is where a
parsed result actually becomes (or updates) a `Food` row.
"""

import hashlib
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from . import off_http
from .off_http import OffRateLimited, OffRequestError  # noqa: F401 (re-exported)

# Overridable (settings.OFF_API_BASE / OFF_SEARCH_BASE) so dev and tests
# can point at OFF's staging server, world.openfoodfacts.net.
API_BASE = getattr(settings, "OFF_API_BASE", "https://world.openfoodfacts.org")
SEARCH_BASE = getattr(settings, "OFF_SEARCH_BASE", "https://search.openfoodfacts.org")
# kcal per 100g/100ml is OFF's own universal unit for every product,
# regardless of that product's real-world serving size — matches this
# app's own "per serving_size of serving_unit" Food shape directly
# when serving_size=100.
PER_100_SERVING_SIZE = Decimal("100")

# Only what `parse_product` reads — Search-a-licious and the v2 product
# API both return just these, keeping responses small.
PRODUCT_FIELDS = (
    "code,product_name,generic_name,brands,nutriments,nutriscore_grade,"
    "nova_group,image_front_url,image_front_thumb_url,image_url,"
    "image_thumb_url,categories,quantity,ingredients_text,labels,"
    "allergens,product_quantity_unit"
)
SEARCH_PAGE_SIZE = 20
# A repeated search costs nothing: OFF asks that one API call map to
# one real user action, and its search budget is only 10/minute per IP.
SEARCH_CACHE_SECONDS = 60 * 60 * 24


class OpenFoodFactsError(off_http.OffRequestError):
    """Raised for a network/parse failure — never for "no results,"
    which is a normal, silently-empty outcome, not an error."""


class OpenFoodFactsRateLimited(OpenFoodFactsError, off_http.OffRateLimited):
    """No request was sent: our own OFF budget is spent, or OFF
    failed moments ago (apps.nutrition.off_http)."""


def _get_json(url, *, bucket, params=None):
    try:
        return off_http.get(url, bucket=bucket, params=params).json()
    except off_http.OffRateLimited as exc:
        raise OpenFoodFactsRateLimited(str(exc)) from exc
    except off_http.OffRequestError as exc:
        raise OpenFoodFactsError(str(exc)) from exc
    except ValueError as exc:
        raise OpenFoodFactsError(str(exc)) from exc


def _search(q, *, page_size, language="en"):
    """One Search-a-licious query (OFF's supported full-text search —
    the old /cgi/search.pl is deprecated). Cached per query."""
    key = "nutrition:off_search:" + hashlib.sha256(
        f"{language}|{page_size}|{q}".encode()
    ).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    payload = _get_json(
        f"{SEARCH_BASE}/search",
        bucket="search",
        params={
            "q": q,
            "page_size": page_size,
            "langs": language,
            "fields": PRODUCT_FIELDS,
        },
    )
    hits = payload.get("hits", [])
    cache.set(key, hits, SEARCH_CACHE_SECONDS)
    return hits


def search_products(query, *, page_size=SEARCH_PAGE_SIZE, language="en"):
    """Free-text search — returns a list of raw OFF product dicts
    (unparsed; call `parse_product` on each to get this app's shape).
    """
    return _search(query, page_size=page_size, language=language)


def get_product(barcode):
    """A single raw OFF product dict by barcode, or `None` if OFF has
    no such product (a normal outcome, not an error)."""
    try:
        response = off_http.get(
            f"{API_BASE}/api/v2/product/{barcode}.json",
            bucket="read",
            params={"fields": PRODUCT_FIELDS},
        )
    except off_http.OffRateLimited as exc:
        raise OpenFoodFactsRateLimited(str(exc)) from exc
    except off_http.OffRequestError as exc:
        # OFF answers 404 (with status 0) for an unknown barcode.
        if getattr(getattr(exc.__cause__, "response", None), "status_code", None) == 404:
            return None
        raise OpenFoodFactsError(str(exc)) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenFoodFactsError(str(exc)) from exc
    if payload.get("status") != 1:
        return None
    return payload.get("product")


# A short curated list rather than OFF's /categories.json (tens of
# thousands of tags, a multi-megabyte download nobody needs): the
# handful of top-level food groups worth a "browse" shortcut. IDs are
# OFF's own canonical category tags.
BROWSE_CATEGORIES = [
    ("en:breakfast-cereals", _("Breakfast cereals")),
    ("en:breads", _("Breads")),
    ("en:dairies", _("Dairy")),
    ("en:cheeses", _("Cheeses")),
    ("en:yogurts", _("Yogurts")),
    ("en:meats", _("Meats")),
    ("en:fishes", _("Fish")),
    ("en:eggs", _("Eggs")),
    ("en:fruits", _("Fruits")),
    ("en:vegetables", _("Vegetables")),
    ("en:legumes", _("Legumes")),
    ("en:nuts", _("Nuts")),
    ("en:pastas", _("Pasta")),
    ("en:rices", _("Rice")),
    ("en:snacks", _("Snacks")),
    ("en:chocolates", _("Chocolate")),
    ("en:biscuits", _("Biscuits")),
    ("en:plant-based-milk-alternatives", _("Plant-based milks")),
    ("en:beverages", _("Beverages")),
    ("en:protein-bars", _("Protein bars")),
]


def list_categories():
    """The curated browse-by-category shortcuts (id + name) — static,
    so it costs no OFF request and can't fail."""
    return [{"id": cid, "name": name} for cid, name in BROWSE_CATEGORIES]


def search_by_category(category_id, *, page_size=SEARCH_PAGE_SIZE):
    """Raw OFF product dicts in one category, via Search-a-licious
    (`categories_tags` filter)."""
    if not category_id.replace(":", "").replace("-", "").isalnum():
        return []
    return _search(
        f'categories_tags:"{category_id}"', page_size=page_size
    )


def _text(value):
    """Text field that may arrive as a list (Search-a-licious)."""
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return value or ""


def _first_brand(brands):
    """`brands` is a comma-separated string from the v2 product API but
    a list from Search-a-licious."""
    if isinstance(brands, (list, tuple)):
        return str(brands[0]).strip() if brands else ""
    return (brands or "").split(",")[0].strip()


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
    if calories is None and nutriments.get("energy-kj_100g") is not None:
        # Many products (Search-a-licious returns them as-is) only
        # carry kJ; 1 kcal = 4.184 kJ.
        calories = float(nutriments["energy-kj_100g"]) / 4.184
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
        "brand": _first_brand(raw.get("brands")),
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
        # Both `None` unless OFF has actually graded this product —
        # "unknown"/"not-applicable" (OFF's own placeholders for "not
        # graded") map to None rather than a misleading guess.
        "nutri_score": (
            raw.get("nutriscore_grade")
            if raw.get("nutriscore_grade") in {"a", "b", "c", "d", "e"}
            else None
        ),
        "nova_group": (
            int(raw["nova_group"]) if raw.get("nova_group") in (1, 2, 3, 4) else None
        ),
        # `image_front_url` — OFF's own already-sized "front of pack"
        # photo, not `image_url` (their largest/raw upload) — see
        # Food.image_url's own comment for why this is linked rather
        # than downloaded. Empty string, not None, when OFF has no
        # photo for this product: both URLField(blank=True) and the
        # `{% if food.image_url %}` template check treat "" as
        # "nothing to show" identically to a genuinely missing key.
        "image_url": raw.get("image_front_url") or raw.get("image_url") or "",
        # OFF's own smaller pre-generated variant — see Food.
        # image_thumb_url's own comment for why this is a separate
        # stored field rather than a resize of image_url.
        "image_thumb_url": raw.get("image_front_thumb_url") or raw.get("image_thumb_url") or "",
        "categories": _text(raw.get("categories")),
        "quantity": _text(raw.get("quantity")),
        "ingredients_text": _text(raw.get("ingredients_text")),
        "labels": _text(raw.get("labels")),
        "allergens": _text(raw.get("allergens")),
    }
