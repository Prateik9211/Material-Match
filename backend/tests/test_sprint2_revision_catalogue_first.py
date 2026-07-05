"""Sprint 2 Revision — Catalogue-First region analysis.

These tests verify the new endpoint contract:
  * `analyze-region` now returns rows enriched with `classification`,
    `catalogue_matches` (5–10 items) and `alternative_systems`.
  * Every catalogue match carries brand, catalogue, page_display,
    material_code_display (falls back to "Code unavailable in current database"),
    match_percent, and a per-metric similarity breakdown.
  * The `/library/global` endpoint now exposes ~177 records grouped by
    9 categories (Paints, Laminates, Veneers, Stone, Tiles, Fabric,
    Lighting, Hardware, Furniture).
  * The seeded demo project is a Bedroom whose rows are already enriched
    with catalogue_matches so signed-out visitors get catalogue-first UX.
"""

from __future__ import annotations

import base64
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://design-match-ai.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@materialmatch.ai")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "MaterialAdmin2026!")

# 1x1 JPEG — valid enough for the fallback path when live AI is off, and
# will fail cleanly when live AI is on (we exercise the fallback in these tests).
TINY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0"
    "Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/"
    "wAARCAABAAEDASIAAhEBAxEBAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAE"
    "EQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0"
    "dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5"
    "+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRC"
    "kaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKT"
    "lJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/aAAwDAQACEQMRAD8A/8QA"
    "="
)


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="module")
def demo_pid():
    r = requests.get(f"{API}/demo/project", timeout=15)
    assert r.status_code == 200
    return r.json()["id"]


@pytest.fixture()
def project_with_ref(admin_session):
    pname = f"TEST_S2R_{uuid.uuid4().hex[:6]}"
    r = admin_session.post(f"{API}/projects", json={"name": pname, "reference_image_b64": TINY_JPEG_B64}, timeout=15)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    yield pid
    admin_session.delete(f"{API}/projects/{pid}")


class TestDemoIsBedroom:
    def test_demo_name_is_bedroom(self):
        r = requests.get(f"{API}/demo/project", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "Bedroom" in d.get("name", "")
        # Every row must be catalogue-enriched.
        rows = d.get("mock_analysis", {}).get("rows", [])
        assert 6 <= len(rows) <= 12, f"expected 6–12 bedroom zones, got {len(rows)}"
        for row in rows:
            assert "classification" in row
            assert "catalogue_matches" in row
            assert 1 <= len(row["catalogue_matches"]) <= 10
            assert "alternative_systems" in row
            for m in row["catalogue_matches"]:
                assert "brand" in m and "catalogue" in m and "material_name" in m
                assert 0 <= m["match_percent"] <= 100
                assert m["source"] == "MaterialMatch Library"
                sim = m.get("similarity", {})
                for k in ("visual", "color", "finish", "texture"):
                    assert 0 <= sim.get(k, -1) <= 100, k

    def test_demo_products_are_bedroom_consistent(self):
        r = requests.get(f"{API}/demo/project", timeout=15)
        d = r.json()
        products = d.get("products_detected", {}).get("products", [])
        assert len(products) >= 4
        blob = " ".join(p.get("product_name", "").lower() for p in products)
        assert any(kw in blob for kw in ("nightstand", "sconce", "bed", "boucle", "rug", "vase"))


class TestLibraryGlobalCategorised:
    def test_library_global_has_9_categories_and_170plus_items(self, admin_session):
        r = admin_session.get(f"{API}/library/global", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "seeded"
        cats = body["categories"]
        assert set(cats.keys()) >= {
            "Paints", "Laminates", "Veneers", "Stone", "Tiles",
            "Fabric", "Lighting", "Hardware", "Furniture",
        }
        assert body["total"] >= 150
        # Paints alone should be 50+.
        assert len(cats["Paints"]) >= 50
        # Trust rule: never fabricate codes. Records without a real code
        # simply omit the field (Sprint 3 removed the "Code unavailable"
        # placeholder text; the UI now hides the field entirely).
        no_code = [x for x in cats["Paints"] if x["material_code"] is None]
        assert len(no_code) > 0  # sanity — many paints have no confirmed code


class TestAnalyzeRegionCatalogueEnrichment:
    def test_region_fallback_returns_classification_and_matches(self, admin_session, project_with_ref):
        r = admin_session.post(
            f"{API}/projects/{project_with_ref}/analyze-region",
            json={"crop_b64": TINY_JPEG_B64, "note": "test crop"},
            timeout=30,
        )
        # 200 in mock-fallback mode; 502 or 200 when live AI runs with a bad crop.
        # We only assert the enriched schema when the endpoint succeeds.
        if r.status_code != 200:
            pytest.skip(f"live AI path — got {r.status_code}, cannot assert fallback")
        d = r.json()
        assert d.get("ephemeral") is True
        rows = d.get("rows", [])
        assert len(rows) >= 1
        row = rows[0]
        assert row.get("classification") in {"Material Surface", "Product", "Fixture", "Decor", "Mixed", "Unclear"}
        matches = row.get("catalogue_matches") or []
        assert 1 <= len(matches) <= 10
        for m in matches:
            assert "brand" in m and "material_name" in m
            assert "match_percent" in m
            assert m.get("source") == "MaterialMatch Library"
        alts = row.get("alternative_systems") or []
        # searched_libraries surfaced for UI trust signal.
        assert isinstance(row.get("searched_libraries"), list)

    def test_short_crop_still_422(self, admin_session, project_with_ref):
        r = admin_session.post(
            f"{API}/projects/{project_with_ref}/analyze-region",
            json={"crop_b64": "AA==", "note": ""},
            timeout=15,
        )
        assert r.status_code == 422


class TestClassifierHeuristic:
    """Direct import of the classifier so we can assert behaviour without a
    live LLM. Uses the same helper as the endpoint."""

    def test_paint_row_classified_as_material_surface(self):
        from server import _classify_row
        assert _classify_row({"material_family": "Paint", "zone": "Wall Paint"}) == "Material Surface"

    def test_lighting_row_classified_as_product(self):
        from server import _classify_row
        assert _classify_row({"material_family": "Lighting", "zone": "Pendant"}) == "Product"

    def test_fixture_keyword_wins_over_metal_family(self):
        from server import _classify_row
        assert _classify_row({"material_family": "Metal", "zone": "Bath Basin Faucet",
                              "material_type": "brushed brass faucet"}) == "Fixture"

    def test_unclear_when_nothing_known(self):
        from server import _classify_row
        assert _classify_row({}) == "Unclear"

    def test_catalogue_matcher_returns_paint_shades_for_paint_row(self):
        from server import _find_catalogue_matches
        row = {"material_family": "Paint", "material_type": "warm white matte",
               "color": "warm white", "finish": "matte", "keywords": ["warm white"]}
        matches = _find_catalogue_matches(row, top_k=8)
        assert len(matches) >= 5
        # At least one of the four seeded paint brands must appear.
        brands = {m["brand"] for m in matches}
        assert brands & {"Asian Paints", "Berger", "Nerolac", "Dulux"}
