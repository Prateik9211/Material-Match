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
