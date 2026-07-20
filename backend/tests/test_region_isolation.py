"""Cross-region isolation proof for the 2026-02-08 multi-region rollout.

Founder requirement: a US record must NEVER surface when region=IN, and
vice versa. This is the same rigor level as the library_scope isolation
test from Scope #1.

Talks to the live backend on localhost:8001; seeds fake catalogue rows
directly into `ke_records`, exercises `_find_catalogue_matches`, and
asserts every result belongs to the requested region.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]

# Pre-seed fixture: one canary record per region, all in the same
# category with distinguishable colours so the matcher will consider
# each one for a matching query.
FIXTURES = [
    # (region, id_suffix, color_hex, material_name)
    ("IN", "in-canary-1", "#8B5A2B", "IN Canary Walnut Laminate"),
    ("IN", "in-canary-2", "#F5F1EC", "IN Canary Cream Paint"),
    ("US", "us-canary-1", "#8B5A2B", "US Canary Walnut Laminate"),
    ("US", "us-canary-2", "#F5F1EC", "US Canary Cream Paint"),
    ("AE", "ae-canary-1", "#8B5A2B", "AE Canary Walnut Laminate"),
    ("AE", "ae-canary-2", "#F5F1EC", "AE Canary Cream Paint"),
]


def _seed_canaries():
    from intelligence.dna import dna_from_record
    for region, id_suffix, hex_color, name in FIXTURES:
        rec = {
            "id": f"canary-{id_suffix}",
            "brand": f"{region} Test Brand",
            "collection_name": f"{region} Test Collection",
            "material_name": name,
            "material_family": "Laminate",
            "category": "Laminate",
            "finish": "Matte",
            "color_name": "Test",
            "color_hex": hex_color,
            "texture": "smooth",
            "keywords": ["laminate", "test"],
            "page_number": 1,
            "region": region,
            "catalogue_scope": "admin",
            "status": "published",
            "demo_seed": False,
            "created_at": datetime.now(timezone.utc),
            "published_at": datetime.now(timezone.utc),
        }
        # Materialise visual_dna so the retrieval scorer will see this
        # record — `intelligence.retrieval.retrieve` skips items without DNA.
        rec["visual_dna"] = dna_from_record(rec)
        _db.ke_records.update_one(
            {"id": rec["id"]},
            {"$set": rec},
            upsert=True,
        )


def _clean_canaries():
    _db.ke_records.delete_many({"id": {"$regex": "^canary-"}})


@pytest.fixture(scope="module", autouse=True)
def _setup_and_teardown():
    _seed_canaries()
    # Force the studio index to rebuild so the new canaries are indexed.
    import server
    asyncio.get_event_loop().run_until_complete(server._refresh_studio_index())
    yield
    _clean_canaries()
    asyncio.get_event_loop().run_until_complete(server._refresh_studio_index())


def _query_row(hex_color: str = "#8B5A2B") -> dict:
    """Build a materialmatch_brain-ready row that WILL hit all 6 canaries."""
    return {
        "object_type": "surface",
        "material_family": "Laminate",
        "color": "Walnut",
        "color_hex": hex_color,
        "finish": "Matte",
        "gloss_level": "low",
        "texture": "smooth",
        "confidence": 80,
        "surface_description": "matte walnut laminate panel",
    }


def _matches_for(region: str, hex_color: str = "#8B5A2B") -> list:
    import server
    row = _query_row(hex_color)
    # materialmatch_brain sets allowed_categories; run it so retrieval
    # gate is realistic
    brain = server.materialmatch_brain(row)
    return server._find_catalogue_matches(
        row, top_k=10, min_overall=10,
        allowed_categories=brain["allowed_categories"] or [],
        weights=brain["ranking_weights"],
        object_locked=bool(brain.get("object_locked")),
        library_scope="admin",
        region=region,
    )


def test_admin_index_partitioned_by_region():
    """After refresh, the studio index MUST be region-partitioned."""
    import server
    in_slice = server._admin_index_for_region("IN")
    us_slice = server._admin_index_for_region("US")
    ae_slice = server._admin_index_for_region("AE")
    # Canaries land in each slice
    assert any("canary-in-" in i["id"] for i in in_slice), "IN slice missing canaries"
    assert any("canary-us-" in i["id"] for i in us_slice), "US slice missing canaries"
    assert any("canary-ae-" in i["id"] for i in ae_slice), "AE slice missing canaries"
    # Cross-region leak check: US canaries must NEVER be in IN slice.
    for it in in_slice:
        assert not it["id"].startswith("canary-us-"), \
            f"US canary {it['id']} leaked into IN slice"
        assert not it["id"].startswith("canary-ae-"), \
            f"AE canary {it['id']} leaked into IN slice"
    for it in us_slice:
        assert not it["id"].startswith("canary-in-")
        assert not it["id"].startswith("canary-ae-")
    for it in ae_slice:
        assert not it["id"].startswith("canary-in-")
        assert not it["id"].startswith("canary-us-")


def test_search_region_in_never_returns_us_or_ae_records():
    hits = _matches_for("IN")
    hit_ids = [h["id"] for h in hits]
    # ISOLATION INVARIANT: NO US or AE canary can leak in, regardless
    # of whether IN canaries themselves ranked high (they compete with
    # 350+ real records so may not surface in top-10 — that's fine).
    us_leaks = [hid for hid in hit_ids if hid.startswith("canary-us-")]
    ae_leaks = [hid for hid in hit_ids if hid.startswith("canary-ae-")]
    assert not us_leaks, f"US canaries leaked into region=IN: {us_leaks}"
    assert not ae_leaks, f"AE canaries leaked into region=IN: {ae_leaks}"


def test_search_region_us_never_returns_in_or_ae_records():
    hits = _matches_for("US")
    hit_ids = [h["id"] for h in hits]
    # US should return the US canary (and nothing else IN/AE).
    assert any(hid.startswith("canary-us-") for hid in hit_ids), \
        f"expected US canary in results (US catalogue is empty apart from canaries), got: {hit_ids}"
    assert not any(hid.startswith("canary-in-") for hid in hit_ids), \
        f"IN canaries leaked into region=US: {hit_ids}"
    assert not any(hid.startswith("canary-ae-") for hid in hit_ids), \
        f"AE canaries leaked into region=US: {hit_ids}"


def test_search_region_ae_never_returns_in_or_us_records():
    hits = _matches_for("AE")
    hit_ids = [h["id"] for h in hits]
    # AE should return the AE canary (and nothing else IN/US).
    assert any(hid.startswith("canary-ae-") for hid in hit_ids), \
        f"expected AE canary in results, got: {hit_ids}"
    assert not any(hid.startswith("canary-in-") for hid in hit_ids)
    assert not any(hid.startswith("canary-us-") for hid in hit_ids)


def test_seeded_catalogue_only_appears_in_region_in():
    """SEEDED_CATALOGUE (built-in demo library) is India-only content —
    it must NEVER appear in US/AE searches."""
    import server
    # Use a color that will match seeded records (they're mostly wood/stone tones)
    in_hits = _matches_for("IN", hex_color="#a07856")  # oak-ish
    us_hits = _matches_for("US", hex_color="#a07856")
    ae_hits = _matches_for("AE", hex_color="#a07856")

    seeded_ids = {item["id"] for item in server.SEEDED_CATALOGUE}
    in_seeded = [h["id"] for h in in_hits if h["id"] in seeded_ids]
    us_seeded = [h["id"] for h in us_hits if h["id"] in seeded_ids]
    ae_seeded = [h["id"] for h in ae_hits if h["id"] in seeded_ids]
    # Seeded records SHOULD appear on IN, and MUST NOT appear on US/AE.
    assert in_seeded, "expected some SEEDED_CATALOGUE hits on IN"
    assert not us_seeded, f"SEEDED_CATALOGUE leaked into US: {us_seeded}"
    assert not ae_seeded, f"SEEDED_CATALOGUE leaked into AE: {ae_seeded}"


def test_region_normaliser_migrates_legacy_values():
    import server
    assert server._normalize_region("India") == "IN"
    assert server._normalize_region("Global") == "IN"  # default fallback
    assert server._normalize_region("IN") == "IN"
    assert server._normalize_region("us") == "US"
    assert server._normalize_region("UAE") == "AE"
    assert server._normalize_region("") == "IN"
    assert server._normalize_region(None) == "IN"
    assert server._normalize_region("garbage") == "IN"


def test_serpapi_lens_country_and_retailer_lists_per_region():
    from intelligence.product_search import (
        _REGION_TO_LENS_COUNTRY, _REGIONAL_RETAILER_HOSTS,
        _is_regional_retailer,
    )
    # Google Lens country codes wired correctly
    assert _REGION_TO_LENS_COUNTRY["IN"] == "in"
    assert _REGION_TO_LENS_COUNTRY["US"] == "us"
    assert _REGION_TO_LENS_COUNTRY["AE"] == "ae"
    # Each region has its own retailer allow-list
    assert "amazon.in" in _REGIONAL_RETAILER_HOSTS["IN"]
    assert "amazon.com" in _REGIONAL_RETAILER_HOSTS["US"]
    assert "amazon.ae" in _REGIONAL_RETAILER_HOSTS["AE"]
    # No cross-region host collisions between IN/US/AE
    assert not any(h in _REGIONAL_RETAILER_HOSTS["US"]
                   for h in _REGIONAL_RETAILER_HOSTS["IN"] if "amazon" not in h)
    # Regional retailer checks work
    assert _is_regional_retailer("https://www.amazon.in/dp/X", "IN")
    assert not _is_regional_retailer("https://www.amazon.in/dp/X", "US")
    assert _is_regional_retailer("https://www.wayfair.com/xyz", "US")
    assert not _is_regional_retailer("https://www.wayfair.com/xyz", "IN")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
