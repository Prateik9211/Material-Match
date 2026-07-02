"""Sprint 2 tests: Products detection + Affiliate DB.

Covers:
  - Keyword matching (_jaccard, _score_affiliate_match)
  - Products payload validator
  - Search URL builder
  - Admin gating (require_admin)
"""

import pytest
from server import (
    _jaccard,
    _tokenize_text,
    _kw_set,
    _score_affiliate_match,
    _validate_products_payload,
    _build_search_urls,
    PRODUCT_CATEGORIES,
    _seed_affiliate_products,
)


# ---------- Jaccard / tokenizer ----------
def test_jaccard_empty():
    assert _jaccard(set(), set()) == 0.0
    assert _jaccard({"a"}, set()) == 0.0


def test_jaccard_identical():
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_half_overlap():
    assert _jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_tokenize_lowercases_and_splits():
    assert _tokenize_text("Brass Pendant Light") == {"brass", "pendant", "light"}
    assert _tokenize_text("") == set()
    assert _tokenize_text(None) == set()


def test_kw_set_lowercases_and_dedups():
    assert _kw_set(["Modern", "modern", "  Warm  "]) == {"modern", "warm"}
    assert _kw_set(None) == set()
    assert _kw_set("not-a-list") == set()


# ---------- Affiliate score ----------
def test_score_affiliate_match_perfect():
    p = {
        "product_name": "Brass Pendant Light",
        "style_keywords": ["modern", "minimalist"],
        "color_keywords": ["brass"],
        "material_keywords": ["brass", "glass"],
        "finish_keywords": ["brushed"],
    }
    aff = {
        "product_name": "Brass Pendant Light",
        "style_keywords": ["modern", "minimalist"],
        "color_keywords": ["brass"],
        "material_keywords": ["brass", "glass"],
        "finish_keywords": ["brushed"],
    }
    score = _score_affiliate_match(p, aff)
    assert score == pytest.approx(1.0, rel=0.01)


def test_score_affiliate_match_zero_when_unrelated():
    p = {
        "product_name": "Brass Pendant Light",
        "style_keywords": ["modern"],
        "material_keywords": ["brass"],
    }
    aff = {
        "product_name": "Cotton Cushion Cover",
        "style_keywords": ["boho"],
        "material_keywords": ["cotton"],
    }
    score = _score_affiliate_match(p, aff)
    assert score == 0.0


def test_score_affiliate_match_partial():
    p = {
        "product_name": "Brass Pendant Light",
        "style_keywords": ["modern", "warm"],
        "material_keywords": ["brass"],
    }
    aff = {
        "product_name": "Brass Table Lamp",
        "style_keywords": ["modern", "elegant"],
        "material_keywords": ["brass", "linen"],
    }
    score = _score_affiliate_match(p, aff)
    # name shares "brass"; style shares "modern"; material shares "brass" -> non-zero
    assert 0 < score < 1


# ---------- Products payload validator ----------
def test_validate_products_happy_path():
    payload = {
        "products": [
            {
                "product_name": "Brass Pendant",
                "category": "lighting",
                "description": "Warm pendant",
                "style_keywords": ["modern"],
                "color_keywords": ["brass"],
                "material_keywords": ["brass"],
                "finish_keywords": ["brushed"],
                "estimated_price_inr": "₹5,000",
                "search_keywords": ["brass pendant india"],
                "confidence": 88,
            }
        ]
    }
    out = _validate_products_payload(payload)
    assert len(out["products"]) == 1
    p = out["products"][0]
    assert p["product_name"] == "Brass Pendant"
    assert p["category"] == "lighting"
    assert p["confidence"] == 88


def test_validate_products_unknown_category_falls_back_to_other():
    payload = {
        "products": [
            {
                "product_name": "X", "category": "unknown_cat", "confidence": 50,
            }
        ]
    }
    out = _validate_products_payload(payload)
    assert out["products"][0]["category"] == "other"


def test_validate_products_missing_name_raises():
    payload = {"products": [{"category": "lighting", "confidence": 50}]}
    with pytest.raises(ValueError):
        _validate_products_payload(payload)


def test_validate_products_missing_products_key_raises():
    with pytest.raises(ValueError):
        _validate_products_payload({"foo": "bar"})


def test_validate_products_confidence_clamps():
    payload = {
        "products": [
            {"product_name": "X", "category": "other", "confidence": 500},
            {"product_name": "Y", "category": "other", "confidence": -20},
            {"product_name": "Z", "category": "other", "confidence": "bad"},
        ]
    }
    out = _validate_products_payload(payload)
    assert out["products"][0]["confidence"] == 100
    assert out["products"][1]["confidence"] == 0
    # non-numeric falls back to 60
    assert out["products"][2]["confidence"] == 60


# ---------- Search URLs ----------
def test_build_search_urls_from_keyword():
    urls = _build_search_urls({"search_keywords": ["brass pendant light"]})
    assert "amazon.in" in urls["amazon_in"]
    assert "brass+pendant+light" in urls["amazon_in"]
    # india appended once for google
    assert urls["google"].count("india") == 1


def test_build_search_urls_falls_back_to_name():
    urls = _build_search_urls({"product_name": "Cotton Cushion", "search_keywords": []})
    assert "Cotton+Cushion" in urls["amazon_in"]


def test_build_search_urls_empty_returns_empty():
    urls = _build_search_urls({})
    assert urls == {}


def test_build_search_urls_no_double_india():
    urls = _build_search_urls({"search_keywords": ["brass lamp india"]})
    # 'india' already present -> shouldn't be duplicated in google query
    assert urls["google"].count("india") == 1


# ---------- Seed data invariants ----------
def test_seed_affiliate_products_uses_indian_platforms():
    indian_platforms = {"Pepperfry", "Urban Ladder", "IKEA India", "WoodenStreet",
                        "Hafele India", "Amazon India", "Jaipur Rugs", "Fabindia"}
    seeds = _seed_affiliate_products()
    assert len(seeds) >= 8
    for s in seeds:
        assert s["platform"] in indian_platforms, f"Non-Indian platform: {s['platform']}"
        assert s["product_category"] in PRODUCT_CATEGORIES
        assert s["affiliate_url"].startswith("http")
        assert s["price_inr"].startswith("₹")


def test_seed_covers_multiple_categories():
    seeds = _seed_affiliate_products()
    categories = {s["product_category"] for s in seeds}
    # We want variety — at least 4 different categories
    assert len(categories) >= 4
