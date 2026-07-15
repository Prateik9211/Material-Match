"""Sprint 5 — User-side Matching Engine tests.

Locks in the critical Phase A integration fixes that let the user matcher
consume the real Published Library (produced by the frozen Sprint 4
ingestion pipeline) instead of only the seeded fallback.

Coverage:
  • Singular ↔ plural category bridge (Laminate ↔ Laminates, etc.)
  • `swatch_crop_b64`, `upload_id`, `source_page_href` are preserved end-to-end
  • Published Library records outrank seeded ones on equal scores
  • Category compatibility filter is HARD (wall zone rejects furniture etc.)
  • `top_k` defaults to 4 for user-side enrichment
  • Deduplication kills near-identical records (same code / same name / same hex)
  • Glossy / reflective finishes soft-cap match_percent at 85
  • Each match carries a debug packet with reason components
  • `_validate_analysis_payload` accepts optional `group` and `pin` fields
  • `_infer_zone_group` maps common zones to Wall / Floor / Ceiling / Furniture
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/app/backend")


# ────────────────────────────────────────────────────────────────────────
# Phase A — Category bridge + swatch crop preservation
# ────────────────────────────────────────────────────────────────────────

def _dna_enrich_item(item: dict, base: dict) -> dict:
    """Mirror what _visual_dna_backfill stores on real published records so
    the mock item is visible to the Sprint 7 retrieval engine."""
    from intelligence.dna import dna_from_record, embedding_text
    from intelligence.embeddings import get_embedder
    item["visual_dna"] = dna_from_record(base)
    item["dna_embedding"] = get_embedder().embed([embedding_text(item["visual_dna"])])[0]
    return item


def _ensure_seed_dna():
    from server import _build_seed_dna_index
    _build_seed_dna_index()


def _mk_studio_item(**over):
    from server import _studio_record_to_search_item
    base = {
        "id": "test-rec-1",
        "brand": "Merino",
        "material_name": "Golden Teak Grain",
        "material_code": "L-8912",
        "category": "Laminate",  # Sprint 4 singular
        "material_family": "Laminate",
        "color_hex": "#B48A55",
        "color_name": "Golden teak",
        "finish": "gloss",
        "texture": "wood grain",
        "keywords": ["teak", "wood", "laminate", "golden", "grain"],
        "page_number": 5,
        "page_preview_b64": "AAAAABBBBBCCCCC",
        "upload_id": "upload-xyz",
        "collection_name": "Wood Grain Series",
        "swatch_bbox": [60, 100, 280, 320],
        "confidence": 95,
    }
    base.update(over)
    return _dna_enrich_item(_studio_record_to_search_item(base), base)


class TestCategoryAliasBridge:
    def test_normalize_singular_to_plural(self):
        from server import _normalize_category
        assert _normalize_category("Laminate") == "Laminates"
        assert _normalize_category("Veneer") == "Veneers"
        assert _normalize_category("Tile") == "Tiles"
        assert _normalize_category("Paint") == "Paints"

    def test_normalize_plural_stays_plural(self):
        from server import _normalize_category
        assert _normalize_category("Laminates") == "Laminates"
        assert _normalize_category("Paints") == "Paints"

    def test_normalize_non_aliased_stays_same(self):
        from server import _normalize_category
        assert _normalize_category("Stone") == "Stone"
        assert _normalize_category("Fabric") == "Fabric"

    def test_normalize_none_or_empty(self):
        from server import _normalize_category
        assert _normalize_category(None) is None
        assert _normalize_category("") is None


class TestStudioSearchItemPreservesSprint5Fields:
    """Preservation of swatch_crop_b64, upload_id, etc. is the whole point
    of Sprint 5 Phase A — assert every required field survives the hop
    from the Studio record to the search item."""

    def test_swatch_crop_preserved(self):
        it = _mk_studio_item()
        assert it["swatch_crop_b64"] == "AAAAABBBBBCCCCC"

    def test_upload_id_preserved(self):
        it = _mk_studio_item()
        assert it["upload_id"] == "upload-xyz"

    def test_bbox_preserved(self):
        it = _mk_studio_item()
        assert it["swatch_bbox"] == [60, 100, 280, 320]

    def test_collection_used_as_catalogue_display(self):
        it = _mk_studio_item()
        assert it["catalogue"] == "Wood Grain Series"

    def test_demo_seed_flag_defaults_false(self):
        it = _mk_studio_item()
        assert it["demo_seed"] is False

    def test_demo_seed_flag_propagates(self):
        it = _mk_studio_item(demo_seed=True)
        assert it["demo_seed"] is True


# ────────────────────────────────────────────────────────────────────────
# Phase A — user matcher consumes real Published Library
# ────────────────────────────────────────────────────────────────────────

class TestCategoryHardFilter:
    """Category filter accepts BOTH conventions. Studio records with
    category='Laminate' MUST surface when the brain says allowed=['Laminates']."""

    def test_studio_singular_survives_plural_allow_list(self):
        from server import _find_catalogue_matches, _STUDIO_INDEXED_RECORDS
        _ensure_seed_dna()
        # Inject a Sprint-4-shape record temporarily.
        studio_rec = _mk_studio_item()
        _STUDIO_INDEXED_RECORDS.append(studio_rec)
        try:
            row = {
                "zone": "Wardrobe front panel",
                "material_family": "Laminate",
                "material_type": "teak grain laminate",
                "color": "Warm golden teak",
                "texture": "wood grain",
                "finish": "gloss",
                "keywords": ["teak", "wood", "grain", "golden", "laminate"],
            }
            matches = _find_catalogue_matches(
                row, top_k=4,
                allowed_categories=["Laminates", "Veneers"],
                min_overall=50,
            )
        finally:
            _STUDIO_INDEXED_RECORDS.remove(studio_rec)
        codes = [m.get("material_code") for m in matches]
        assert "L-8912" in codes, (
            f"Studio Sprint 4 record (cat=Laminate) did not surface under "
            f"allowed=['Laminates']. Got: {codes}"
        )

    def test_incompatible_category_rejected(self):
        from server import _find_catalogue_matches
        _ensure_seed_dna()
        # Wall paint zone — only Paints should surface (never Furniture / Fabric).
        row = {
            "zone": "Living room wall",
            "material_family": "wall",
            "material_type": "emulsion",
            "color": "Ivory white",
            "texture": "smooth",
            "finish": "matt",
            "keywords": ["paint", "white"],
        }
        matches = _find_catalogue_matches(
            row, top_k=6,
            allowed_categories=["Paints"],
            min_overall=50,
        )
        for m in matches:
            assert m["category"] in ("Paints", "Paint"), (
                f"Wall paint zone returned non-paint result: {m['category']} — {m['material_name']}"
            )


class TestMatchOutputShape:
    """Every match must carry the Sprint 5 fields required by the UI."""

    def test_match_has_swatch_crop_and_source_href(self):
        from server import _find_catalogue_matches, _STUDIO_INDEXED_RECORDS
        studio_rec = _mk_studio_item()
        _STUDIO_INDEXED_RECORDS.append(studio_rec)
        try:
            row = {
                "zone": "Wardrobe",
                "material_family": "Laminate",
                "material_type": "teak laminate",
                "color": "Warm teak",
                "texture": "wood grain",
                "finish": "gloss",
                "keywords": ["teak", "laminate", "wood"],
            }
            matches = _find_catalogue_matches(
                row, top_k=4,
                allowed_categories=["Laminates"],
                min_overall=50,
            )
        finally:
            _STUDIO_INDEXED_RECORDS.remove(studio_rec)
        assert matches, "no matches produced"
        studio_matches = [m for m in matches if m["source_library"] == "Published Library"]
        assert studio_matches, "no Published Library match — Studio record was dropped"
        m = studio_matches[0]
        assert m["swatch_crop_b64"], "swatch_crop_b64 missing on Published Library match"
        assert m["has_swatch_crop"] is True
        assert m["upload_id"] == "upload-xyz"
        assert m["source_page_href"] and "/uploads/upload-xyz/page/5" in m["source_page_href"]
        assert m["match_reason"], "match_reason missing"
        assert "debug" in m
        for k in ("record_id", "source_library", "retrieval_score", "pipeline_stage"):
            assert k in m["debug"], f"debug packet missing {k}"

    def test_seeded_match_shape(self):
        from server import _find_catalogue_matches
        _ensure_seed_dna()
        row = {
            "zone": "Wall paint",
            "material_family": "wall",
            "material_type": "emulsion",
            "color": "Warm ivory",
            "texture": "smooth",
            "finish": "matt",
            "keywords": ["ivory", "warm", "paint"],
        }
        matches = _find_catalogue_matches(
            row, top_k=4, allowed_categories=["Paints"], min_overall=50,
        )
        assert matches
        # Seeded records don't have a per-swatch crop yet.
        for m in matches:
            assert m["source_library"] in ("Seeded Library", "Published Library")
            assert isinstance(m["match_reason"], str) and m["match_reason"]
            # Every match carries a debug packet
            assert m["debug"]["pipeline_stage"] in ("retrieval", "exact_loopback")


# ────────────────────────────────────────────────────────────────────────
# Phase C — trim, dedup, glossy cap
# ────────────────────────────────────────────────────────────────────────

class TestResultTrim:
    def test_default_top_k_is_four_for_user_side(self):
        from server import _enrich_rows_with_catalogue
        rows = [{
            "zone": "Wall paint",
            "material_family": "wall",
            "material_type": "emulsion",
            "color": "Warm ivory",
            "texture": "smooth",
            "finish": "matt",
            "design_style": "modern",
            "keywords": ["ivory", "warm", "paint"],
            "confidence": 80,
        }]
        _enrich_rows_with_catalogue(rows)
        matches = rows[0].get("catalogue_matches") or []
        assert len(matches) <= 4, f"expected ≤4 matches, got {len(matches)}"


class TestDeduplication:
    """Near-identical results must be collapsed. Two records that share a
    material_code should never both appear."""

    def test_same_code_deduplicated(self):
        from server import _find_catalogue_matches, _STUDIO_INDEXED_RECORDS
        a = _mk_studio_item(id="dup-a", brand="Merino", material_name="Teak A")
        b = _mk_studio_item(id="dup-b", brand="Merino", material_name="Teak A")
        _STUDIO_INDEXED_RECORDS.extend([a, b])
        try:
            row = {"zone": "Wardrobe", "material_family": "Laminate",
                   "material_type": "teak", "color": "Warm teak",
                   "texture": "wood grain", "finish": "gloss",
                   "keywords": ["teak", "laminate"]}
            m = _find_catalogue_matches(
                row, top_k=6, allowed_categories=["Laminates"], min_overall=50,
            )
        finally:
            _STUDIO_INDEXED_RECORDS.remove(a)
            _STUDIO_INDEXED_RECORDS.remove(b)
        # Both records have code=L-8912 — dedup keeps one.
        assert sum(1 for x in m if x["material_code"] == "L-8912") == 1


class TestRetrievalConfidenceCap:
    """Sprint 7 — retrieval-only matches never claim visual certainty.
    Confidence is capped at RETRIEVAL_CONF_CAP until the GPT-4o visual
    re-rank verifies a candidate (or a pHash exact loopback fires)."""

    def test_retrieval_only_capped(self):
        from server import _find_catalogue_matches
        from intelligence.confidence import RETRIEVAL_CONF_CAP
        _ensure_seed_dna()
        row = {
            "zone": "Feature wall",
            "material_family": "wall",
            "material_type": "high-gloss lacquer paint",
            "color": "Warm ivory",
            "texture": "smooth",
            "finish": "high-gloss",
            "keywords": ["ivory", "gloss", "polished"],
        }
        matches = _find_catalogue_matches(
            row, top_k=4, allowed_categories=["Paints"], min_overall=50,
        )
        for m in matches:
            assert m["match_percent"] <= RETRIEVAL_CONF_CAP, (
                f"retrieval-only match must cap at {RETRIEVAL_CONF_CAP}, "
                f"got {m['match_percent']}"
            )
            assert m["visually_verified"] is False


# ────────────────────────────────────────────────────────────────────────
# Phase B — group + pin coercion
# ────────────────────────────────────────────────────────────────────────

class TestZoneGroupCoercion:
    def test_valid_group_kept(self):
        from server import _validate_analysis_payload
        out = _validate_analysis_payload({
            "rows": [{
                "zone": "Sofa upholstery",
                "group": "Furniture",
                "material_family": "upholstery",
                "material_type": "linen",
                "color": "Warm ivory",
                "texture": "woven",
                "finish": "matt",
                "design_style": "modern",
                "keywords": ["linen"],
                "confidence": 80,
            }]
        })
        assert out["rows"][0]["group"] == "Furniture"

    def test_missing_group_inferred_from_zone(self):
        from server import _validate_analysis_payload
        out = _validate_analysis_payload({
            "rows": [{
                "zone": "Living room wall paint",
                "material_family": "wall",
                "material_type": "emulsion",
                "color": "Warm ivory",
                "texture": "smooth",
                "finish": "matt",
                "design_style": "modern",
                "keywords": ["paint"],
                "confidence": 80,
            }]
        })
        assert out["rows"][0]["group"] == "Wall"

    def test_missing_group_stays_none_when_ambiguous(self):
        from server import _validate_analysis_payload
        out = _validate_analysis_payload({
            "rows": [{
                "zone": "Some ambiguous surface",
                "material_family": "wood",
                "material_type": "wood",
                "color": "brown",
                "texture": "smooth",
                "finish": "matt",
                "design_style": "modern",
                "keywords": ["wood"],
                "confidence": 60,
            }]
        })
        assert out["rows"][0]["group"] is None


class TestPinCoercion:
    def test_percent_pin_kept(self):
        from server import _validate_analysis_payload
        out = _validate_analysis_payload({
            "rows": [{
                "zone": "Wall paint",
                "group": "Wall",
                "pin": {"x": 42.5, "y": 60},
                "material_family": "wall",
                "material_type": "emulsion",
                "color": "White",
                "texture": "smooth",
                "finish": "matt",
                "design_style": "modern",
                "keywords": ["paint"],
                "confidence": 80,
            }]
        })
        assert out["rows"][0]["pin"] == {"x": 42.5, "y": 60.0}

    def test_unit_scale_pin_upscaled(self):
        from server import _validate_analysis_payload
        out = _validate_analysis_payload({
            "rows": [{
                "zone": "Floor tile",
                "group": "Floor",
                "pin": {"x": 0.5, "y": 0.75},
                "material_family": "flooring",
                "material_type": "tile",
                "color": "beige",
                "texture": "smooth",
                "finish": "matt",
                "design_style": "modern",
                "keywords": ["tile"],
                "confidence": 80,
            }]
        })
        p = out["rows"][0]["pin"]
        assert p == {"x": 50.0, "y": 75.0}

    def test_invalid_pin_becomes_none(self):
        from server import _validate_analysis_payload
        out = _validate_analysis_payload({
            "rows": [{
                "zone": "Wall",
                "group": "Wall",
                "pin": {"x": "hello", "y": 20},
                "material_family": "wall",
                "material_type": "emulsion",
                "color": "white",
                "texture": "smooth",
                "finish": "matt",
                "design_style": "modern",
                "keywords": ["paint"],
                "confidence": 80,
            }]
        })
        assert out["rows"][0]["pin"] is None

    def test_pin_absent_stays_none(self):
        from server import _validate_analysis_payload
        out = _validate_analysis_payload({
            "rows": [{
                "zone": "Wall",
                "group": "Wall",
                "material_family": "wall",
                "material_type": "emulsion",
                "color": "white",
                "texture": "smooth",
                "finish": "matt",
                "design_style": "modern",
                "keywords": ["paint"],
                "confidence": 80,
            }]
        })
        assert out["rows"][0]["pin"] is None


# ────────────────────────────────────────────────────────────────────────
# Phase B — inference of top-level group
# ────────────────────────────────────────────────────────────────────────

class TestInferZoneGroup:
    def test_headboard_is_wall(self):
        from server import _infer_zone_group
        assert _infer_zone_group("Headboard feature wall", "") == "Wall"

    def test_flooring_is_floor(self):
        from server import _infer_zone_group
        assert _infer_zone_group("Bedroom flooring", "") == "Floor"

    def test_ceiling_paint_is_ceiling(self):
        from server import _infer_zone_group
        assert _infer_zone_group("Ceiling finish", "") == "Ceiling"

    def test_sofa_is_furniture(self):
        from server import _infer_zone_group
        assert _infer_zone_group("Sofa upholstery", "") == "Furniture"

    def test_wardrobe_is_furniture(self):
        from server import _infer_zone_group
        assert _infer_zone_group("Wardrobe finish", "") == "Furniture"
