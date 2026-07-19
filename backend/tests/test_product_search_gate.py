"""Unit tests for `intelligence.product_search`.

Confirms the quality gate correctly:
  * PASSES the 4 crops that returned good SerpApi results in the
    2026-02-08 feasibility test (sofa/chair-tight, planter, table_lamp).
  * FAILS the pendant_light case (tall/thin aspect ratio → returned 10
    unrelated web-design pages, zero shoppable results).
  * FAILS crops that are too small, too big, low confidence, or in an
    excluded category.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from intelligence.product_search import (  # noqa: E402
    passes_quality_gate,
    prepare_crop_bytes,
    crop_cache_key,
    crop_sha_from_key,
    _normalize_match,
    _looks_shoppable,
    GATE_MIN_CROP_SIDE_PX,
    GATE_MAX_CROP_SIDE_PX,
    GATE_MAX_ASPECT_RATIO,
)


def _prod(**over) -> dict:
    base = {
        "product_name": "Fabric Sofa",
        "category": "furniture",
        "confidence": 82,
        "sam3_bbox": [100, 100, 400, 250],
        "sam3_confidence": 0.82,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Feasibility-test cases — the ones that either worked or failed on 2026-02-08.
# ---------------------------------------------------------------------------
def test_feasibility_planter_passes():
    """planter crop was 206x400 → aspect 1.94, category plant-planter."""
    p = _prod(product_name="Tall Bird of Paradise Planter",
              category="plant-planter",
              sam3_bbox=[284, 314, 161, 311],
              sam3_confidence=0.75)
    d = passes_quality_gate(p)
    assert d.passed, f"planter should pass, got: {d.reason}"


def test_feasibility_table_lamp_passes():
    """table_lamp crop 110x112 was on the tight side but square."""
    p = _prod(product_name="Table Lamp",
              category="lighting",
              sam3_bbox=[1080, 319, 110, 112],
              sam3_confidence=0.71)
    d = passes_quality_gate(p)
    assert d.passed, f"table lamp should pass, got: {d.reason}"


def test_feasibility_chair_passes():
    """dining chair crop 217x231, category furniture."""
    p = _prod(product_name="Dining Chair",
              category="furniture",
              sam3_bbox=[905, 527, 217, 231],
              sam3_confidence=0.73)
    d = passes_quality_gate(p)
    assert d.passed, f"chair should pass, got: {d.reason}"


def test_feasibility_pendant_light_fails_aspect_ratio():
    """The pendant_light disaster: 92x800 → aspect 8.70. My gate rejects
    aspect > 3.5 while still allowing horizontal sofa strips (aspect ~3.4)."""
    p = _prod(product_name="Pendant Light",
              category="lighting",
              sam3_bbox=[519, 92, 92, 800],   # extreme vertical
              sam3_confidence=0.60)
    d = passes_quality_gate(p)
    assert not d.passed
    assert "aspect_ratio" in d.reason, f"expected aspect_ratio fail, got: {d.reason}"


def test_feasibility_sofa_wide_strip_passes():
    """Feasibility confirmed a 363x108 sofa strip (aspect 3.36) returned
    59 real matches — the gate must NOT reject it."""
    p = _prod(product_name="Fabric Sofa", category="furniture",
              sam3_bbox=[613, 782, 363, 108],
              sam3_confidence=0.75)
    d = passes_quality_gate(p)
    assert d.passed, f"sofa strip should pass, got: {d.reason}"


def test_feasibility_pendant_light_fails_size_when_massive():
    """Alternate failure mode: crop wider than 900 px (mostly context)."""
    p = _prod(product_name="Pendant Light",
              category="lighting",
              sam3_bbox=[100, 100, 950, 700],
              sam3_confidence=0.7)
    d = passes_quality_gate(p)
    assert not d.passed
    assert "too_large" in d.reason


# ---------------------------------------------------------------------------
# Independent gate rules.
# ---------------------------------------------------------------------------
def test_gate_missing_bbox():
    p = _prod(sam3_bbox=None)
    assert not passes_quality_gate(p).passed


def test_gate_empty_bbox():
    p = _prod(sam3_bbox=[0, 0, 0, 0])
    assert not passes_quality_gate(p).passed


def test_gate_too_small():
    p = _prod(sam3_bbox=[100, 100, 30, 30])
    d = passes_quality_gate(p)
    assert not d.passed
    assert "too_small" in d.reason


def test_gate_low_confidence():
    p = _prod(sam3_confidence=0.45)
    d = passes_quality_gate(p)
    assert not d.passed
    assert "low_confidence" in d.reason


def test_gate_low_confidence_percent_scale():
    # Detection might record confidence as 0-100 instead of 0-1.
    p = _prod(sam3_confidence=None, confidence=40)
    d = passes_quality_gate(p)
    assert not d.passed
    assert "low_confidence" in d.reason


def test_gate_excluded_category_without_product_token():
    p = _prod(product_name="Persian Runner", category="art",
              sam3_confidence=0.85)
    d = passes_quality_gate(p)
    assert not d.passed
    assert "category_excluded" in d.reason


def test_gate_excluded_category_with_product_token_passes():
    """Even category='other', if the name says 'lamp' it should pass."""
    p = _prod(product_name="Arc Floor Lamp", category="other",
              sam3_confidence=0.75)
    d = passes_quality_gate(p)
    assert d.passed


# ---------------------------------------------------------------------------
# Crop prep + cache-key helpers.
# ---------------------------------------------------------------------------
def test_prepare_crop_produces_reasonably_sized_jpeg():
    from PIL import Image
    img = Image.new("RGB", (1400, 1400), (200, 180, 150))
    b = prepare_crop_bytes(img, [100, 100, 400, 250], pad_frac=0.08)
    # JPEG magic bytes
    assert b[:3] == b"\xff\xd8\xff"
    assert 500 < len(b) < 200_000


def test_cache_key_stable_and_content_addressed():
    from PIL import Image
    img = Image.new("RGB", (1000, 1000), (100, 50, 200))
    b = prepare_crop_bytes(img, [10, 10, 400, 400])
    k1 = crop_cache_key(b, country="in")
    k2 = crop_cache_key(b, country="in")
    assert k1 == k2
    assert k1 != crop_cache_key(b, country="us")
    assert crop_sha_from_key(k1) == k1.split("_")[0]


# ---------------------------------------------------------------------------
# Normalise / shoppable helpers.
# ---------------------------------------------------------------------------
def test_normalize_match_trims_boilerplate_and_extracts_price():
    m = {
        "title": "Buy XYZ Fabric Sofa in Grey Colour Online at Best Price ",
        "source": "Amazon.in",
        "price": {"value": "\u20b910,999", "extracted_value": 10999, "currency": "INR"},
        "link": "https://www.amazon.in/dp/B01",
        "thumbnail": "https://x/y.jpg",
    }
    n = _normalize_match(m)
    assert n["title"] == "XYZ Fabric Sofa in Grey Colour"
    assert n["source"] == "Amazon.in"
    assert n["price_value"] == 10999
    assert n["currency"] == "INR"
    assert n["link"] == "https://www.amazon.in/dp/B01"


def test_looks_shoppable_india_hosts():
    assert _looks_shoppable("https://www.amazon.in/xyz", "Amazon.in")
    assert _looks_shoppable("https://www.pepperfry.com/product/abc", "Pepperfry")
    assert _looks_shoppable("https://www.urbanladder.com/product/x", "Urban Ladder")


def test_looks_shoppable_rejects_junk_sources():
    assert not _looks_shoppable("https://youtube.com/watch?v=x", "YouTube")
    assert not _looks_shoppable("https://behance.net/xyz", "Behance")
    assert not _looks_shoppable("https://someone.blogspot.com/2020", "Blog")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
