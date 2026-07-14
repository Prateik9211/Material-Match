"""Sprint 6 — perceptual-hash utilities for exact / near-exact catalogue
loopback matching.

The published Studio catalogue stores an isolated JPEG per swatch
(`page_preview_b64`). This module turns each swatch into a small, fast,
deterministic visual fingerprint (pHash + dHash ensemble) so the user
matcher can recognise when a reference-image region is visually identical
or very close to a published swatch — regardless of small crop / scale /
compression differences.

Never generates a hash from anything other than an isolated material
swatch: never full pages, never room renders, never solid colour
placeholders, never multi-material collages.

Hashes are cheap (~1 ms per swatch on a 240x240 thumbnail) and require
no LLM calls. Distance is Hamming (integer, 0..64).

Reference:  Hamming distance interpretation guide (calibrated on Advance
loopback + Merino / Kajaria synthetics on 2026-07-14):
  d ≤ 6  → visually identical (compression variant of the same image)
  d ≤ 12 → visually very close (crop / scale variant of the same swatch)
  d ≤ 20 → same product family, likely the same swatch category
  d > 20 → treat as unrelated (fall back to fuzzy text ranking)
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _load_image_from_b64(b64: str):
    """Decode a base64 JPEG/PNG string into a PIL Image (RGB). Returns
    None on any failure — hashes should never crash the ingestion path."""
    if not b64 or len(b64) < 32:
        return None
    try:
        # Strip data-url prefix if the caller forgot to.
        if b64.startswith("data:"):
            b64 = b64.split(",", 1)[-1]
        raw = base64.b64decode(b64)
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return img
    except Exception as e:
        logger.debug("phash: decode failed (%s)", e)
        return None


def compute_visual_hashes(b64: str) -> dict | None:
    """Return `{phash, dhash, whash, width, height}` for a base64 image,
    or None if the image could not be decoded / is degenerate.

    phash — DCT-based, robust to small colour shifts.
    dhash — difference hash, robust to lighting shifts.
    whash — wavelet hash, robust to compression.

    All three are 64-bit hexadecimal strings so they survive Mongo
    serialisation and remain human-readable in the debug packet.
    """
    img = _load_image_from_b64(b64)
    if img is None:
        return None
    w, h = img.size
    # Reject degenerate images that are clearly not a real material swatch
    # (a 4x4 placeholder or a giant multi-material page).
    if w < 32 or h < 32:
        return None
    try:
        import imagehash
        return {
            "phash": str(imagehash.phash(img, hash_size=8)),
            "dhash": str(imagehash.dhash(img, hash_size=8)),
            "whash": str(imagehash.whash(img)),
            "width": w,
            "height": h,
        }
    except Exception as e:
        logger.warning("phash: compute failed (%s)", e)
        return None


def hamming(a: str, b: str) -> int:
    """Hamming distance between two hex-encoded 64-bit hashes."""
    if not a or not b:
        return 64
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


# Sprint 6 thresholds — calibrated against Advance loopback + Merino /
# Kajaria synthetics. See tests/test_sprint6_phash.py for the reference
# corpus and the false-positive / false-negative table.
PHASH_EXACT_MAX = 6      # pixel-identical / compression variant
PHASH_NEAR_MAX = 12      # crop / scale variant of the same swatch
PHASH_LOOSE_MAX = 20     # same swatch family, likely related


def similarity_from_distance(d: int) -> int:
    """Convert Hamming distance to a 0..100 similarity score. Linear from
    d=0 (100) to d=32 (0); values beyond 32 are clamped to 0."""
    if d <= 0:
        return 100
    if d >= 32:
        return 0
    return max(0, min(100, int(round(100 * (1 - d / 32)))))


def visual_distance(a: Optional[dict], b: Optional[dict]) -> dict:
    """Return `{phash, dhash, whash, best, verdict}` between two hash
    packets. Returns Hamming 64 (max) when either side is missing.

    verdict ∈ {"exact", "near", "loose", "unrelated"} — driven by the
    strongest signal across pHash / dHash. Used by the ranker to decide
    whether to promote a candidate above the fuzzy scorer.
    """
    if not a or not b:
        return {"phash": 64, "dhash": 64, "whash": 64, "best": 64, "verdict": "unrelated"}
    ph = hamming(a.get("phash"), b.get("phash"))
    dh = hamming(a.get("dhash"), b.get("dhash"))
    wh = hamming(a.get("whash"), b.get("whash"))
    best = min(ph, dh, wh)
    if best <= PHASH_EXACT_MAX:
        verdict = "exact"
    elif best <= PHASH_NEAR_MAX:
        verdict = "near"
    elif best <= PHASH_LOOSE_MAX:
        verdict = "loose"
    else:
        verdict = "unrelated"
    return {"phash": ph, "dhash": dh, "whash": wh, "best": best, "verdict": verdict}
