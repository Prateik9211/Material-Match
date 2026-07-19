"""Regression tests for the 2026-02-27 founder-reported bug fixes:

  1) Under-detection — SAM3 vocabulary must include wainscot, trim, and
     paneled wainscoting; large architectural surfaces (wall/ceiling/
     floor/backsplash) must use per-label confidence overrides so they
     survive `filter_detections`.
  2) Missing / inconsistent pins — every material row must render a pin
     coordinate on the reference image:
         a) LLM-path rows fall back to a deterministic group-based pin
            when the LLM omits the optional `pin` field.
         b) Scene-mode rows always derive a pin from the SAM3 bbox
            centre (in image %).
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest

from intelligence.scene_segmentation import (
    ARCHITECTURAL_VOCAB,
    LABEL_MIN_CONFIDENCE,
    filter_detections,
)
from server import (
    MATERIAL_FAMILIES,
    _attach_product_pins,
    _dna_to_row,
    _fallback_pin_for_group,
    _validate_analysis_payload,
)


# ---------------------------------------------------------------------------
# Bug 1a — Under-detection: SAM3 vocab must include wainscot / trim / paneled
# ---------------------------------------------------------------------------
def test_architectural_vocab_includes_wainscot_and_trim():
    vocab_lc = {v.lower() for v in ARCHITECTURAL_VOCAB}
    for prompt in ("wainscot", "trim", "paneled wainscoting"):
        assert prompt in vocab_lc, f"missing SAM3 prompt: {prompt!r}"


# ---------------------------------------------------------------------------
# 2026-02-27 (round 4) — Founder business rule: cushion / pillow / mattress
# must NEVER surface as materials.  They are shoppable products (handled
# by _run_products_pipeline).  Headboards are exempt from this rule.
# ---------------------------------------------------------------------------
def test_cushion_pillow_mattress_in_vocab_and_shortcut_none():
    from intelligence.scene_segmentation import DETERMINISTIC_MATERIAL
    vocab_lc = {v.lower() for v in ARCHITECTURAL_VOCAB}
    for prompt in ("cushion", "pillow", "throw pillow", "mattress"):
        assert prompt in vocab_lc, f"missing SAM3 prompt: {prompt!r}"
        assert prompt in DETERMINISTIC_MATERIAL, (
            f"{prompt!r} must be in DETERMINISTIC_MATERIAL for the "
            f"skip-material routing rule"
        )
        assert DETERMINISTIC_MATERIAL[prompt] is None, (
            f"{prompt!r} must map to None (skip material entirely) — "
            f"got {DETERMINISTIC_MATERIAL[prompt]!r}"
        )


def test_headboard_is_NOT_in_skip_material_shortcut():
    """Regression guard: headboards should ALWAYS go through the normal
    LLM material classification (fabric / wood / upholstery). The founder
    rule explicitly exempts headboards."""
    from intelligence.scene_segmentation import DETERMINISTIC_MATERIAL
    vocab_lc = {v.lower() for v in ARCHITECTURAL_VOCAB}
    assert "headboard" in vocab_lc, "headboard must be detectable"
    assert "headboard" not in DETERMINISTIC_MATERIAL, (
        "headboard must NOT have a deterministic shortcut — it must go "
        "through the normal LLM material path so fabric/wood upholstery "
        "still fires"
    )


# ---------------------------------------------------------------------------
# Bug 1b — Under-detection: filter_detections must honour per-label overrides
# so large architectural surfaces at soft-ish confidence (0.35-0.55) are kept.
# ---------------------------------------------------------------------------
def test_filter_detections_keeps_soft_ceiling_and_floor_with_override():
    detections = [
        {"label": "ceiling", "confidence": 0.42, "bbox": [0, 0, 1000, 200]},
        {"label": "floor",   "confidence": 0.38, "bbox": [0, 800, 1000, 200]},
        {"label": "wall",    "confidence": 0.45, "bbox": [0, 200, 1000, 600]},
        {"label": "sofa",    "confidence": 0.60, "bbox": [100, 400, 400, 300]},
        # A 0.30-confidence "wall" is BELOW even the loosened 0.40 gate so
        # should still be filtered out — regression guard against making
        # the gate too loose.
        {"label": "wall",    "confidence": 0.30, "bbox": [0, 200, 100, 100]},
    ]

    kept_default = filter_detections(detections, min_confidence=0.55)
    kept_labels_default = {d["label"] for d in kept_default}

    kept_override = filter_detections(
        detections, min_confidence=0.55,
        label_min_confidence=LABEL_MIN_CONFIDENCE,
    )
    kept_labels_override = {d["label"] for d in kept_override}

    # Default gate drops ceiling/floor/wall at their soft confidences.
    assert "ceiling" not in kept_labels_default
    assert "floor" not in kept_labels_default

    # Override gate rescues them.
    assert "ceiling" in kept_labels_override
    assert "floor" in kept_labels_override
    assert "wall" in kept_labels_override

    # The 0.30 wall is still below its 0.40 override → correctly dropped.
    walls = [d for d in kept_override if d["label"] == "wall"]
    assert all(w["confidence"] >= 0.40 for w in walls)


# ---------------------------------------------------------------------------
# Bug 2a — Deterministic fallback pin per group (LLM path).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("group,y_expected_range", [
    ("Ceiling",   (0, 20)),
    ("Wall",      (35, 55)),
    ("Floor",     (80, 95)),
    ("Furniture", (60, 75)),
])
def test_fallback_pin_positions_are_in_expected_band(group, y_expected_range):
    for i in range(5):
        pin = _fallback_pin_for_group(group, i)
        assert pin is not None, f"expected pin for group {group} idx {i}"
        assert y_expected_range[0] <= pin["y"] <= y_expected_range[1], (
            f"{group}@{i} y={pin['y']} outside {y_expected_range}"
        )
        assert 0 <= pin["x"] <= 100


def test_fallback_pin_none_for_unknown_group():
    assert _fallback_pin_for_group("Other", 0) is None
    assert _fallback_pin_for_group(None, 0) is None
    assert _fallback_pin_for_group("", 0) is None


def test_validate_analysis_payload_fills_missing_llm_pin():
    fam = next(iter(MATERIAL_FAMILIES))
    payload = {
        "rows": [
            # Row 0 — no pin → should get group fallback.
            {"zone": "Ceiling", "group": "Ceiling", "material_family": fam,
             "material_type": "flat", "color": "white", "texture": "smooth",
             "finish": "matte", "design_style": "modern",
             "keywords": ["paint"], "confidence": 80},
            # Row 1 — LLM emitted pin → should be preserved (source=llm).
            {"zone": "Wall", "group": "Wall", "material_family": fam,
             "material_type": "flat", "color": "beige", "texture": "smooth",
             "finish": "matte", "design_style": "modern",
             "keywords": ["paint"], "confidence": 75,
             "pin": {"x": 45.0, "y": 40.0}},
            # Row 2 — no pin → should get group fallback.
            {"zone": "Floor", "group": "Floor", "material_family": fam,
             "material_type": "oak", "color": "honey", "texture": "grain",
             "finish": "matte", "design_style": "modern",
             "keywords": ["wood"], "confidence": 82},
        ],
    }
    result = _validate_analysis_payload(payload)
    rows = result["rows"]
    assert len(rows) == 3

    # Every row must have a pin (previously only LLM-provided pins showed).
    assert all(r["pin"] is not None for r in rows), \
        f"rows missing pin: {[r['pin'] for r in rows]}"

    # Fallback source tagged for debugging.
    assert rows[0]["pin_source"] == "fallback_group"
    assert rows[1]["pin_source"] == "llm"
    assert rows[1]["pin"] == {"x": 45.0, "y": 40.0}
    assert rows[2]["pin_source"] == "fallback_group"


# ---------------------------------------------------------------------------
# Bug 2b — Scene-mode rows: pin derived deterministically from bbox centre.
# ---------------------------------------------------------------------------
def test_dna_to_row_derives_pin_from_bbox_center():
    row = _dna_to_row(
        dna={"material_family": "Wood",
             "primary_color": {"name": "oak", "hex": "#B47D50"}},
        object_label="floor",
        object_confidence=0.7,
        index=0,
        bbox=[100, 800, 800, 200],   # x, y, w, h
        polygon=None,
        source="dna",
        image_size=(1000, 1000),
    )
    assert row["pin"] == {"x": 50.0, "y": 90.0}, row["pin"]
    assert row["pin_source"] == "scene_bbox"
    assert row["group"] == "Floor"


def test_dna_to_row_pin_is_none_without_image_size():
    row = _dna_to_row(
        dna={"material_family": "Wood"},
        object_label="wall",
        object_confidence=0.6,
        index=1,
        bbox=[100, 200, 500, 300],
        polygon=None,
        source="dna",
        image_size=None,
    )
    assert row["pin"] is None


def test_dna_to_row_pin_is_none_without_bbox():
    row = _dna_to_row(
        dna={"material_family": "Wood"},
        object_label="wall",
        object_confidence=0.6,
        index=1,
        bbox=None,
        polygon=None,
        source="dna",
        image_size=(1000, 1000),
    )
    assert row["pin"] is None
    assert row["pin_source"] is None


# ---------------------------------------------------------------------------
# 2026-02-27 (round 2) — Scene-mode default wiring for `/analyze`.
# The real_analyze endpoint should now attempt scene-mode FIRST and
# only fall back to `run_real_analysis` (LLM-only) when SAM3 fails or
# returns zero objects.  These tests unit-test the wrapper logic
# without hitting the live SAM3 API.
# ---------------------------------------------------------------------------
def test_scene_mode_default_wiring_smoke():
    """Scene-mode is the *default* for `/analyze` — verify by inspecting
    the module source. Cheap sentinel test so a future refactor can't
    silently regress back to LLM-only without failing CI."""
    import server as _server
    import inspect
    src = inspect.getsource(_server.real_analyze)
    assert "run_scene_region_analysis" in src, (
        "real_analyze must call the hybrid scene pipeline as the "
        "default path — got source without run_scene_region_analysis"
    )
    assert "run_real_analysis" in src, (
        "real_analyze must retain run_real_analysis as the belt-and-"
        "suspenders LLM-only fallback"
    )
    # The fallback branch must set a `scene_fallback` marker so the
    # frontend / observability can distinguish the two paths.
    assert "scene_fallback" in src, (
        "real_analyze must tag the LLM-only fallback with a "
        "`scene_fallback` reason for observability"
    )
    # Scene-mode success path must use its own version prefix so the
    # existing `real-` dedup check still catches it.
    assert "real-scene-hybrid-v1" in src, (
        "scene-mode success path must use version='real-scene-hybrid-v1'"
    )


# ---------------------------------------------------------------------------
# 2026-02-27 (round 5) — Product → SAM3 bbox pinning.
# ---------------------------------------------------------------------------
_STAGE_A_SAMPLE = {
    "image_size": {"width": 1000, "height": 1000},
    "objects": [
        {"label": "bed",    "confidence": 0.82, "bbox": [200, 400, 600, 400]},
        {"label": "rug",    "confidence": 0.75, "bbox": [150, 800, 700, 180]},
        {"label": "curtain","confidence": 0.72, "bbox": [50, 100, 200, 700]},
        {"label": "mirror", "confidence": 0.68, "bbox": [780, 200, 180, 240]},
        {"label": "pillow", "confidence": 0.66, "bbox": [300, 380, 200, 120]},
    ],
}


def test_attach_product_pins_matches_by_name_keyword():
    products = [
        {"product_name": "Upholstered Bed",   "material_keywords": ["fabric"]},
        {"product_name": "Decorative Rug",    "material_keywords": ["wool"]},
        {"product_name": "Curtains with Grommets", "material_keywords": ["cotton"]},
        {"product_name": "Gold Chandelier",   "material_keywords": ["brass"]},  # no SAM3 match
        {"product_name": "Silk Throw Pillow", "material_keywords": ["silk"]},
    ]
    _attach_product_pins(products, _STAGE_A_SAMPLE)

    # Bed → bbox centre (500, 600) → (50.0%, 60.0%)
    assert products[0]["pin"] == {"x": 50.0, "y": 60.0}
    assert products[0]["pin_source"] == "product_sam3"
    assert products[0]["pin_matched_label"] == "bed"

    # Rug → bbox centre (500, 890) → (50.0%, 89.0%)
    assert products[1]["pin"] == {"x": 50.0, "y": 89.0}
    assert products[1]["pin_matched_label"] == "rug"

    # Curtain
    assert products[2]["pin_source"] == "product_sam3"
    assert products[2]["pin_matched_label"] == "curtain"

    # Chandelier — no SAM3 label maps.  Must NOT get a fake pin.
    assert products[3]["pin"] is None
    assert products[3]["pin_source"] is None

    # Throw pillow → matches "pillow" SAM3 label.
    assert products[4]["pin_source"] == "product_sam3"
    assert products[4]["pin_matched_label"] == "pillow"


def test_attach_product_pins_ties_broken_by_confidence():
    """If two SAM3 detections match the same product, the one with the
    higher confidence wins."""
    stage_a = {
        "image_size": {"width": 1000, "height": 1000},
        "objects": [
            {"label": "sofa", "confidence": 0.60, "bbox": [0, 0, 100, 100]},
            {"label": "sofa", "confidence": 0.85, "bbox": [800, 800, 100, 100]},
        ],
    }
    products = [{"product_name": "Red Fabric Armchair"}]
    _attach_product_pins(products, stage_a)
    # 0.85 sofa wins → centre (850, 850) → (85%, 85%)
    assert products[0]["pin"] == {"x": 85.0, "y": 85.0}


def test_attach_product_pins_noop_without_stage_a():
    products = [{"product_name": "Bed"}]
    _attach_product_pins(products, {})   # empty stage_a → no-op
    assert "pin" not in products[0]      # never touched
    _attach_product_pins(products, {"objects": [], "image_size": {"width": 1000, "height": 1000}})
    assert "pin" not in products[0]


# ---------------------------------------------------------------------------
# 2026-02-27 (round 7) — Cross-family retrieval leak fix.
# The DNA classifier's low-family-confidence + family_alternatives signal
# must NOT override the Brain's object-locked category gate.  A confidently-
# classified matte wall paint must NEVER get matched against a laminate at
# 88% just because BGE thinks the descriptions are semantically similar.
# ---------------------------------------------------------------------------
def test_wall_object_lock_disables_widen():
    """A wall row with object_type='wall', family='Paint' should route
    only to Paints regardless of the DNA classifier's family_alternatives.
    """
    import server as _s
    row = {
        "object_type": "wall",
        "material_family": "Paint",
        "material_type": "matte wall paint",
        "material_confidence": 82,
        # The DNA classifier separately flagged this crop as ambiguous:
        "family_confidence": 0.5,
        "family_alternatives": ["Laminate", "Veneer"],
        "visual_dna": {"family_confidence": 0.5,
                       "family_alternatives": ["Laminate", "Veneer"]},
        "color": "warm white", "color_hex": "#F1EBE0",
    }
    brain = _s.materialmatch_brain(row)
    assert brain["allowed_categories"] == ["Paints"]
    assert brain.get("object_locked") is True, \
        "wall object routing must be marked object_locked=True"


def test_object_locked_skips_widen_in_find_catalogue_matches():
    """When object_locked=True is passed, widen block must NOT add
    Laminates to the search pool even though family_confidence < 0.7."""
    import server as _s
    from unittest.mock import patch

    row = {
        "object_type": "wall",
        "material_family": "Paint",
        "family_confidence": 0.5,
        "family_alternatives": ["Laminate"],
        "visual_dna": {"family_confidence": 0.5,
                       "family_alternatives": ["Laminate"],
                       "material_family": "Paint"},
    }
    captured = {}

    def _spy_retrieve(query_dna, ref_hashes, items, top_k=8):
        captured["categories"] = {(it.get("category") or "").lower() for it in items}
        return {"candidates": [], "meta": {}}

    with patch("intelligence.pipeline.retrieve_matches", side_effect=_spy_retrieve):
        _s._find_catalogue_matches(row, allowed_categories=["Paints"],
                                    object_locked=True)
    # Only Paints reached the retrieval — Laminates was NOT widened in.
    assert "laminates" not in captured.get("categories", set()), \
        f"widen leaked laminates in despite object_locked=True: {captured}"


def test_object_locked_false_still_widens():
    """Regression guard: when object_locked=False (isolated-swatch path),
    the widen SHOULD still fire so genuinely ambiguous flat crops can
    reach the correct family via family_alternatives."""
    import server as _s
    from unittest.mock import patch

    row = {
        "material_family": "Paint",
        "family_confidence": 0.5,
        "family_alternatives": ["Laminate"],
        "visual_dna": {"family_confidence": 0.5,
                       "family_alternatives": ["Laminate"],
                       "material_family": "Paint"},
    }
    captured = {}

    def _spy_retrieve(query_dna, ref_hashes, items, top_k=8):
        captured["categories"] = {(it.get("category") or "").lower() for it in items}
        return {"candidates": [], "meta": {}}

    with patch("intelligence.pipeline.retrieve_matches", side_effect=_spy_retrieve):
        _s._find_catalogue_matches(row, allowed_categories=["Paints"],
                                    object_locked=False)
    # object_locked=False → widen fires → laminates in pool.
    assert "laminates" in captured.get("categories", set()), \
        f"widen should still fire when object_locked=False: {captured}"


def test_alt_family_similarity_downgraded_to_0_7():
    """attribute_similarity for an alt-family match must be 0.7 (not 1.0)
    so same-family matches at slightly weaker signals still outrank
    cross-family semantic-similarity-only matches."""
    from intelligence.retrieval import attribute_similarity
    qdna = {"material_family": "paint",
            "family_confidence": 0.5,
            "family_alternatives": ["laminate"],
            "primary_color": {"hex": "#F1EBE0"}}
    cdna = {"material_family": "laminate",
            "primary_color": {"hex": "#F1EBE0"}}
    attr = attribute_similarity(qdna, cdna)
    assert attr["family"] == 0.7, f"expected 0.7 got {attr['family']}"


# ---------------------------------------------------------------------------
# Round 8 — polygon-centroid pin preference (avoids "rug pin lands on book").
# ---------------------------------------------------------------------------
def test_dna_to_row_prefers_polygon_centroid_over_bbox_center():
    """When a polygon is provided (≥3 vertices), the pin should use the
    polygon CENTROID rather than the bbox centre — for large flat
    surfaces (rug/floor) with objects placed on top, the centroid is
    more likely to fall on visible material."""
    from server import _dna_to_row
    # Rug bbox is 1000×200 at (0, 800); centre = (500, 900).
    # But we give a polygon that skews to the left third — centroid ≈ (250, 900).
    polygon = [
        {"x": 0,   "y": 800},
        {"x": 500, "y": 810},
        {"x": 500, "y": 990},
        {"x": 0,   "y": 990},
    ]
    row = _dna_to_row(
        dna={"material_family": "Fabric",
             "primary_color": {"name": "wool", "hex": "#C0AC90"}},
        object_label="rug",
        object_confidence=0.7,
        index=0,
        bbox=[0, 800, 1000, 200],
        polygon=polygon,
        source="dna",
        image_size=(1000, 1000),
    )
    # Polygon centroid: mean_x=(0+500+500+0)/4 = 250 → 25%; mean_y ≈ 897.5 → 89.75%
    assert row["pin"]["x"] == 25.0
    assert abs(row["pin"]["y"] - 89.8) < 0.5, row["pin"]
    assert row["pin_source"] == "scene_polygon_centroid"


# ---------------------------------------------------------------------------
# Round 8 (retest) — Wall + Other/undefined family must NOT surface a
# confident Laminate match.  Regression for the second issue found by
# the bug testing agent: even with object_locked=True, the Brain's
# fallback for unknown family widened allowed=['Paints','Laminates'],
# which let Laminates outrank Paints on strong color/finish matches.
# Fix: default to Paints only for unknown-family walls/ceilings.
# ---------------------------------------------------------------------------
def test_wall_unknown_family_defaults_to_paints_only():
    """Even when material_family is missing / Other, an object-locked
    wall row should NOT include Laminates in the initial allow-list.
    """
    import server as _s
    for missing_fam in (None, "", "Other", "Unknown"):
        row = {"object_type": "wall",
               "material_family": missing_fam,
               "material_type": "smooth surface",
               "material_confidence": 60}
        brain = _s.materialmatch_brain(row)
        assert brain["object_locked"] is True
        assert brain["allowed_categories"] == ["Paints"], (
            f"unknown-family wall (family={missing_fam!r}) must default "
            f"to ['Paints'] only, got {brain['allowed_categories']}"
        )


def test_wall_unknown_family_widens_only_via_material_type_selfconsistency():
    """The self-consistency widen (based on material_type free-text)
    MUST still fire for the wall case — a wall row whose material_type
    literally says 'laminate panel' should legitimately widen to
    include Laminates."""
    import server as _s
    row = {"object_type": "wall",
           "material_family": None,
           "material_type": "warm oak laminate panel",
           "material_confidence": 65}
    brain = _s.materialmatch_brain(row)
    assert "Paints" in brain["allowed_categories"]
    assert "Laminates" in brain["allowed_categories"], (
        "material_type 'laminate panel' should still widen the "
        "allow-list to include Laminates via self-consistency"
    )


# ---------------------------------------------------------------------------
# Round 8 (retest) — Shortlist API must persist and return swatch_crop_b64,
# color_hex, and material_code so the lightbox can render.
# ---------------------------------------------------------------------------
def test_shortlist_item_persists_swatch_fields():
    """ShortlistItemCreate must accept and add_shortlist_item must
    persist swatch_crop_b64, color_hex, material_code."""
    import server as _s
    fields = _s.ShortlistItemCreate.model_fields
    for f in ("swatch_crop_b64", "color_hex", "material_code"):
        assert f in fields, (
            f"ShortlistItemCreate is missing {f!r} — the click-to-"
            f"enlarge lightbox cannot render without this field"
        )


# ---------------------------------------------------------------------------
# Round 8 (retest 2) — Flooring must ALSO be object_locked so a Tile
# floor with a Laminate alt suggestion doesn't widen into Laminates.
# ---------------------------------------------------------------------------
def test_tile_floor_object_locked_no_laminate_leak():
    """A floor row confidently classified as Tile with family_confidence<0.7
    and family_alternatives=['Laminate'] must NOT widen retrieval into
    Laminates.  Bug testing agent iteration 21 caught this leak — 79%
    Laminate match on a Tile floor.
    """
    import server as _s
    from unittest.mock import patch

    row = {
        "object_type": "floor",
        "material_family": "Tile",
        "material_type": "ivory stone tile floor",
        "family_confidence": 0.5,
        "family_alternatives": ["Laminate"],
        "visual_dna": {"family_confidence": 0.5,
                       "family_alternatives": ["Laminate"],
                       "material_family": "Tile"},
    }
    brain = _s.materialmatch_brain(row)
    assert brain.get("object_locked") is True, (
        f"flooring context must be object_locked, got "
        f"{brain.get('object_locked')!r} for app_ctx="
        f"{brain.get('application_context')!r}"
    )
    captured = {}

    def _spy_retrieve(query_dna, ref_hashes, items, top_k=8):
        captured["categories"] = {(it.get("category") or "").lower()
                                   for it in items}
        return {"candidates": [], "meta": {}}

    with patch("intelligence.pipeline.retrieve_matches",
               side_effect=_spy_retrieve):
        _s._find_catalogue_matches(row,
                                     allowed_categories=brain["allowed_categories"],
                                     object_locked=brain["object_locked"])
    assert "laminates" not in captured.get("categories", set()), (
        f"Tile floor should NOT widen into Laminates via family_alts, "
        f"but retrieval saw: {captured.get('categories')}"
    )


# ---------------------------------------------------------------------------
# Round 9 — #2 vocab additions (feature/accent wall + wall-art).
# ---------------------------------------------------------------------------
def test_feature_wall_and_accent_wall_in_vocab():
    from intelligence.scene_segmentation import ARCHITECTURAL_VOCAB, LABEL_MIN_CONFIDENCE
    vocab_lc = {v.lower() for v in ARCHITECTURAL_VOCAB}
    for prompt in ("feature wall", "accent wall"):
        assert prompt in vocab_lc, f"missing SAM3 prompt: {prompt!r}"
        assert prompt in LABEL_MIN_CONFIDENCE, (
            f"{prompt!r} should have a soft per-label confidence override"
        )


def test_wall_art_prompts_route_to_products_not_materials():
    """Wall art / framed art / painting / picture frame must be in the
    SAM3 vocab AND in DETERMINISTIC_MATERIAL as None so they skip
    material classification.  The parallel products pipeline (which
    already targets 'art frames') will pick them up as shoppable."""
    from intelligence.scene_segmentation import ARCHITECTURAL_VOCAB, DETERMINISTIC_MATERIAL
    vocab_lc = {v.lower() for v in ARCHITECTURAL_VOCAB}
    for prompt in ("wall art", "framed art", "artwork", "painting", "picture frame"):
        assert prompt in vocab_lc, f"missing SAM3 prompt: {prompt!r}"
        assert prompt in DETERMINISTIC_MATERIAL, f"{prompt!r} needs skip-material entry"
        assert DETERMINISTIC_MATERIAL[prompt] is None, (
            f"{prompt!r} must be None (skip material) — got "
            f"{DETERMINISTIC_MATERIAL[prompt]!r}"
        )
