"""SerpApi Google Lens "similar items" client + quality gate.

Founder-approved 2026-02-08 after a 5-crop feasibility test on real SAM3
detections. Key learnings baked into this module:

* This is VISUAL SIMILARITY search, not SKU identification — the caller
  MUST label results as "Similar items" and never as "exact match".
* Result quality collapses on busy / oversized / oddly-shaped crops
  (see the pendant_light feasibility case: 10 unrelated web-design
  pages returned). The `passes_quality_gate` function skips these
  crops entirely so we don't spend a search credit.
* Google Lens fetches by public HTTP URL, not base64. The caller is
  responsible for making the crop reachable via the content-addressed
  `/api/product_search/crop/{sha256}.jpg` endpoint.
* Free plan is 250 searches/month. Cost tracking + a monthly cap is
  enforced OUTSIDE this module (server.py owns the Mongo counter).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "").strip()
SERPAPI_ENDPOINT = "https://serpapi.com/search"
DEFAULT_COUNTRY = "in"  # India — matches the app's current audience
DEFAULT_LANG = "en"
REQUEST_TIMEOUT_S = 45
CROP_MAX_SIDE_PX = 800
CROP_JPEG_QUALITY = 90

# Quality-gate thresholds — tuned against the 2026-02-08 feasibility test.
# See docstring of `passes_quality_gate` for the rationale.
# Note: `prepare_crop_bytes` upscales small crops before sending them to
# Google Lens (feasibility confirmed the 110×112 table-lamp crop worked
# fine once upscaled to ~400px), so the gate's min side is 60px — below
# that the source pixel resolution genuinely has too little detail.
GATE_MIN_CROP_SIDE_PX = 60
GATE_MAX_CROP_SIDE_PX = 900        # much bigger than this drags in context
# Feasibility: sofa crop 363x108 (aspect 3.36) → 59 real results, so
# 3.5 is the empirical safe upper bound. Pendant_light (aspect 8.7)
# still gets rejected — that's the clear failure case.
GATE_MAX_ASPECT_RATIO = 3.5
GATE_MIN_SAM3_CONFIDENCE = 0.6     # Stage-A confidence
# Product categories worth searching. Rugs / art / textile-decor are
# excluded — feasibility showed rugs return blog covers and art returns
# stock-photo pages, i.e. garbage.
GATE_ALLOWED_CATEGORIES = {
    "furniture", "lighting", "plant-planter", "decor", "fixture",
    "electronics", "other",
}
# Whitelist of product NAME tokens that always pass even if category is
# uncertain. Keep in sync with the product-detection prompt.
GATE_STRONG_PRODUCT_TOKENS = {
    "sofa", "couch", "chair", "armchair", "stool", "bench", "ottoman",
    "table", "desk", "shelf", "sideboard", "bed", "cabinet",
    "lamp", "pendant", "sconce", "chandelier", "fixture", "light",
    "planter", "vase", "pot", "plant",
    "mirror", "clock", "artwork",
}


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------
@dataclass
class GateDecision:
    passed: bool
    reason: str


def _rect_from_bbox(bbox) -> Optional[tuple[float, float, float, float]]:
    """Normalise a bbox to `(x, y, w, h)` floats. Returns None on bad input."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        return tuple(float(v) for v in bbox)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def passes_quality_gate(product: dict) -> GateDecision:
    """Return whether it's worth spending a SerpApi search credit on this
    product.

    Rationale for each rule (all derived from the 2026-02-08 feasibility
    test on real SAM3 crops):

    * Missing `sam3_bbox` → we don't know WHERE in the image the product
      is, so we'd have to send the whole reference photo, which returned
      garbage in every trial. Skip.
    * `sam3_confidence < 0.6` → detection is uncertain; crops from these
      often included background objects and produced irrelevant hits.
    * Aspect ratio > 2.6 → tall/thin crops (like the pendant_light test
      case) confused Google Lens into returning web-design portfolio
      pages instead of products.
    * min side < 90 px OR max side > 900 px → too small = no detail,
      too big = mostly context.
    * Category not in `GATE_ALLOWED_CATEGORIES` AND name contains no
      strong product token → probably a rug/art/generic item where
      visual search returns lifestyle photos, not shoppable listings.
    """
    bbox = _rect_from_bbox(product.get("sam3_bbox"))
    if bbox is None:
        return GateDecision(False, "no_bbox")

    _, _, w, h = bbox
    if w <= 0 or h <= 0:
        return GateDecision(False, "empty_bbox")
    if min(w, h) < GATE_MIN_CROP_SIDE_PX:
        return GateDecision(False, f"too_small ({int(min(w, h))}px < {GATE_MIN_CROP_SIDE_PX})")
    if max(w, h) > GATE_MAX_CROP_SIDE_PX:
        return GateDecision(False, f"too_large ({int(max(w, h))}px > {GATE_MAX_CROP_SIDE_PX})")
    ratio = max(w, h) / min(w, h)
    if ratio > GATE_MAX_ASPECT_RATIO:
        return GateDecision(False, f"aspect_ratio_extreme ({ratio:.2f} > {GATE_MAX_ASPECT_RATIO})")

    conf = product.get("sam3_confidence") or product.get("confidence")
    try:
        conf_f = float(conf)
        # Product-detection confidence can arrive as 0-100 or 0-1.
        if conf_f > 1.0:
            conf_f = conf_f / 100.0
    except (TypeError, ValueError):
        conf_f = 0.0
    if conf_f < GATE_MIN_SAM3_CONFIDENCE:
        return GateDecision(False, f"low_confidence ({conf_f:.2f} < {GATE_MIN_SAM3_CONFIDENCE})")

    category = (product.get("category") or "other").strip().lower()
    name = (product.get("product_name") or "").strip().lower()
    if category not in GATE_ALLOWED_CATEGORIES and not any(
        tok in name for tok in GATE_STRONG_PRODUCT_TOKENS
    ):
        return GateDecision(False, f"category_excluded ({category})")

    return GateDecision(True, "ok")


# ---------------------------------------------------------------------------
# Crop preparation
# ---------------------------------------------------------------------------
def prepare_crop_bytes(pil_image, bbox, pad_frac: float = 0.08,
                        min_side_px: int = 400) -> bytes:
    """Crop `pil_image` to `bbox` (x, y, w, h in pixels) with `pad_frac`
    padding on each side.

    Sizing: crops smaller than `min_side_px` on the shortest side are
    UPSCALED (LANCZOS) — feasibility testing confirmed Google Lens
    performs better on ~400px crops even when the source SAM3 detection
    was only ~110px (small products photographed in wide-angle rooms).
    Crops larger than `CROP_MAX_SIDE_PX` are downsized. Returns JPEG bytes.
    """
    from PIL import Image
    x, y, w, h = [float(v) for v in bbox]
    W, H = pil_image.size
    pad_x = int(w * pad_frac)
    pad_y = int(h * pad_frac)
    x0 = max(0, int(x - pad_x))
    y0 = max(0, int(y - pad_y))
    x1 = min(W, int(x + w + pad_x))
    y1 = min(H, int(y + h + pad_y))
    crop = pil_image.crop((x0, y0, x1, y1))
    cw, ch = crop.size
    # Upscale tiny crops so Google Lens has enough detail.
    if min(cw, ch) < min_side_px:
        scale = min_side_px / max(1, min(cw, ch))
        crop = crop.resize(
            (max(1, int(cw * scale)), max(1, int(ch * scale))),
            Image.LANCZOS,
        )
    if max(crop.size) > CROP_MAX_SIDE_PX:
        crop.thumbnail((CROP_MAX_SIDE_PX, CROP_MAX_SIDE_PX), Image.LANCZOS)
    buf = io.BytesIO()
    crop.convert("RGB").save(buf, "JPEG", quality=CROP_JPEG_QUALITY)
    return buf.getvalue()


def crop_cache_key(crop_bytes: bytes, country: str = DEFAULT_COUNTRY) -> str:
    """Content-addressed cache key: SHA-256 of the crop bytes, plus country
    (so US and IN searches on the same crop don't collide)."""
    h = hashlib.sha256(crop_bytes).hexdigest()
    return f"{h}_{country}"


def crop_sha_from_key(cache_key: str) -> str:
    """Extract just the sha256 hex (used as the URL segment for the public
    crop endpoint)."""
    return cache_key.split("_", 1)[0]


# ---------------------------------------------------------------------------
# SerpApi Google Lens
# ---------------------------------------------------------------------------
_INDIA_RETAILER_HOSTS = (
    "amazon.in", ".flipkart.", "pepperfry.", "urbanladder.", "myntra.",
    "woodenstreet.", "ikea.com/in", "whiteteak.", "godrejinterio.",
    "nilkamalfurniture.", "hometown.in", "decorest.", "wakefit.",
    "sleepycat.", "decathlon.in",
)
# International retailers still count as shoppable — better a real
# Amazon.com listing than nothing. The UI already labels this as
# "Similar items" (not exact matches), so cross-border discovery is
# honest UX.
_GLOBAL_RETAILER_HOSTS = (
    "amazon.", "wayfair.", "ikea.com", "westelm.", "cb2.", "target.com",
    "walmart.com", "houzz.", "overstock.", "etsy.com", "aliexpress.",
    "made.com", "dunelm.", "johnlewis.", "ebay.",
)
# Sources / hosts we always drop — feasibility runs confirmed these are
# stock-photo, blog, or 3D-model sites, never shoppable.
_BANNED_HOSTS = (
    "youtube.", "behance.net", "pinterest.", "instagram.", ".blogspot.",
    "wordpress.", "medium.com", "cgtrader.", "3dbrute.", "turbosquid.",
    "sketchfab.", "yellowimages.", "flickr.", "reddit.", "quora.",
    "twitter.", "facebook.com", "linkedin.",
)


def _looks_shoppable(link: str, source: str) -> bool:
    """Return True if link is a genuine retail product page. Filters out
    stock-photo / social / blog sites that Google Lens surfaces on
    ambiguous crops."""
    l = (link or "").lower()
    if any(b in l for b in _BANNED_HOSTS):
        return False
    if any(host in l for host in _INDIA_RETAILER_HOSTS):
        return True
    if any(host in l for host in _GLOBAL_RETAILER_HOSTS):
        return True
    # Fall back on source-string heuristics for unlisted retailers.
    s = (source or "").lower()
    return bool(s and ("shop" in s or "store" in s or "furniture" in s))


def _is_indian_retailer(link: str) -> bool:
    return any(host in (link or "").lower() for host in _INDIA_RETAILER_HOSTS)


def _normalize_match(m: dict) -> dict:
    """Flatten a SerpApi visual_match record into the shape the frontend
    consumes. Returns None-ish fields when data is missing."""
    price_obj = m.get("price")
    if isinstance(price_obj, dict):
        price_display = price_obj.get("value")
        price_extracted = price_obj.get("extracted_value")
        currency = price_obj.get("currency")
    else:
        price_display = price_obj
        price_extracted = m.get("extracted_price")
        currency = None
    title = (m.get("title") or "").strip()
    # Trim boilerplate prefixes/suffixes from Google-scraped titles.
    for prefix in ("Buy ", "buy "):
        if title.startswith(prefix):
            title = title[len(prefix):]
    if " Online at " in title:
        title = title.split(" Online at ")[0]
    if len(title) > 110:
        title = title[:107] + "…"
    return {
        "title": title,
        "source": m.get("source") or "",
        "price_display": price_display,
        "price_value": price_extracted,
        "currency": currency,
        "link": m.get("link") or "",
        "thumbnail": m.get("thumbnail") or "",
    }


class ProductSearchError(RuntimeError):
    """Raised when SerpApi returns a hard error (not just empty)."""


def search_similar_by_url(image_url: str, country: str = DEFAULT_COUNTRY,
                          lang: str = DEFAULT_LANG,
                          max_results: int = 6) -> dict:
    """Call SerpApi Google Lens with a public image URL. Returns:

        {
          "similar_items": [ {title, source, price_display, price_value,
                              currency, link, thumbnail, region}, ... ],
          "raw_match_count": int,
          "shoppable_count": int,
          "indian_count": int,
          "elapsed_s": float,
          "empty": bool,
          "error": str | None,
        }

    Behaviour tuned after the 2026-02-08 feasibility test:
      * `country` is deliberately NOT sent to Google Lens — feasibility
        confirmed `country=in` returns 0 matches for many valid crops
        (Google Lens's India shopping index is sparse). Instead we get
        global results and rank Indian retailers first.
      * We DROP every non-shoppable result (YouTube, blogs, 3D-model
        sites, stock-photo hosts) before ranking. Empty results are
        preferable to junk results per the founder's spec.
      * Priced Indian retailer > priced global retailer > unpriced Indian
        > unpriced global.

    Callers must have already: (a) budgeted the search, (b) confirmed the
    quality gate passed, (c) exposed `image_url` publicly.
    """
    if not SERPAPI_KEY:
        raise ProductSearchError("SERPAPI_KEY missing from environment")
    params = {
        "engine": "google_lens",
        "type": "visual_matches",
        "url": image_url,
        "api_key": SERPAPI_KEY,
        "hl": lang,
        # `country` intentionally omitted — see docstring.
    }
    url = SERPAPI_ENDPOINT + "?" + urllib.parse.urlencode(params)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_S) as r:
            d = json.loads(r.read().decode())
    except Exception as e:
        raise ProductSearchError(f"SerpApi request failed: {e}") from e
    elapsed = time.monotonic() - t0

    if d.get("search_metadata", {}).get("status") == "Error":
        raise ProductSearchError(f"SerpApi error: {d.get('error')}")

    vm = d.get("visual_matches") or []

    # Keep only shoppable retail hits.
    shoppable = [
        m for m in vm
        if _looks_shoppable(m.get("link") or "", m.get("source") or "")
    ]
    indian_count = sum(1 for m in shoppable if _is_indian_retailer(m.get("link") or ""))

    # Rank: (a) Indian retailer, (b) has a price, (c) original position.
    ranked = sorted(
        enumerate(shoppable),
        key=lambda ix: (
            not _is_indian_retailer(ix[1].get("link") or ""),
            not bool(ix[1].get("price")),
            ix[0],
        ),
    )
    normalized = [_normalize_match(m) for _, m in ranked[:max_results]]

    logger.info(
        "[serpapi] elapsed=%.1fs raw=%d shoppable=%d indian=%d kept=%d",
        elapsed, len(vm), len(shoppable), indian_count, len(normalized),
    )

    return {
        "similar_items": normalized,
        "raw_match_count": len(vm),
        "shoppable_count": len(shoppable),
        "indian_count": indian_count,
        "elapsed_s": round(elapsed, 2),
        "empty": len(normalized) == 0,
        "error": d.get("error"),
    }
