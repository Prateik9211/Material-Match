#!/usr/bin/env python3
"""
Focused backend verification for founder-reported cross-family catalogue matching regression.
Creates direct evidence for:
- materialmatch_brain object-locked wall Paint routing
- _find_catalogue_matches search pool gating when object_locked=True vs False
- attribute_similarity alt-family penalty
- _dna_to_row polygon centroid pin preference
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path('/app/backend')
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

report = {"checks": []}


def check(name, passed, details=None):
    report["checks"].append({"name": name, "passed": bool(passed), "details": details or {}})
    print(f"{'PASS' if passed else 'FAIL'}: {name} {details or ''}")


import server as s
from intelligence.retrieval import attribute_similarity

# 1) Brain locks architectural wall Paint to Paints only.
wall_row = {
    "object_type": "wall",
    "material_family": "Paint",
    "material_type": "matte wall paint",
    "material_confidence": 82,
    "family_confidence": 0.5,
    "family_alternatives": ["Laminate", "Veneer"],
    "visual_dna": {"material_family": "Paint", "family_confidence": 0.5, "family_alternatives": ["Laminate", "Veneer"]},
    "color": "warm white",
    "color_hex": "#F1EBE0",
}
brain = s.materialmatch_brain(wall_row)
check(
    "object-locked wall Paint brain routing",
    brain.get("object_locked") is True and brain.get("allowed_categories") == ["Paints"],
    {"brain": {k: brain.get(k) for k in ["object_locked", "allowed_categories", "allowed_libraries"]}},
)

# 2) Retrieval pool must not include Laminates when object_locked=True.
captured_locked = {}


def spy_locked(query_dna, ref_hashes, items, top_k=8):
    captured_locked["categories"] = sorted({(it.get("category") or "") for it in items})
    return {"candidates": [], "meta": {}}


with patch("intelligence.pipeline.retrieve_matches", side_effect=spy_locked):
    s._find_catalogue_matches(dict(wall_row), allowed_categories=["Paints"], object_locked=True)
locked_cats = captured_locked.get("categories", [])
check(
    "object_locked=True prevents low-confidence widen into Laminates",
    "Laminates" not in locked_cats and "Paints" in locked_cats,
    {"retrieval_categories": locked_cats},
)

# 3) Same ambiguity still widens for isolated-swatch/object_locked=False.
captured_unlocked = {}


def spy_unlocked(query_dna, ref_hashes, items, top_k=8):
    captured_unlocked["categories"] = sorted({(it.get("category") or "") for it in items})
    return {"candidates": [], "meta": {}}


with patch("intelligence.pipeline.retrieve_matches", side_effect=spy_unlocked):
    s._find_catalogue_matches(dict(wall_row), allowed_categories=["Paints"], object_locked=False)
unlocked_cats = captured_unlocked.get("categories", [])
check(
    "object_locked=False still widens ambiguous isolated swatches",
    "Laminates" in unlocked_cats and "Paints" in unlocked_cats,
    {"retrieval_categories": unlocked_cats},
)

# 4) Alt-family score is penalized to 0.7, not 1.0.
attr = attribute_similarity(
    {"material_family": "paint", "family_confidence": 0.5, "family_alternatives": ["laminate"], "primary_color": {"hex": "#F1EBE0"}},
    {"material_family": "laminate", "primary_color": {"hex": "#F1EBE0"}},
)
check("alt-family attribute_similarity family penalty is 0.7", attr.get("family") == 0.7, {"attribute_similarity": attr})

# 5) Polygon centroid pin preferred over bbox center.
row_with_poly = s._dna_to_row(
    {"material_family": "Fabric", "surface_type": "rug", "primary_color": {"name": "beige", "hex": "#d0c0aa"}},
    object_label="rug",
    object_confidence=0.9,
    index=0,
    bbox=[0, 800, 1000, 200],
    polygon=[{"x": 100, "y": 820}, {"x": 300, "y": 820}, {"x": 350, "y": 980}, {"x": 250, "y": 980}],
    source="dna",
    image_size=(1000, 1000),
)
row_bbox_only = s._dna_to_row(
    {"material_family": "Fabric", "surface_type": "rug", "primary_color": {"name": "beige", "hex": "#d0c0aa"}},
    object_label="rug",
    object_confidence=0.9,
    index=0,
    bbox=[0, 800, 1000, 200],
    polygon=None,
    source="dna",
    image_size=(1000, 1000),
)
check(
    "_dna_to_row uses polygon centroid when polygon exists and bbox otherwise",
    row_with_poly.get("pin_source") == "scene_polygon_centroid" and row_with_poly.get("pin") == {"x": 25.0, "y": 90.0} and row_bbox_only.get("pin_source") == "scene_bbox" and row_bbox_only.get("pin") == {"x": 50.0, "y": 90.0},
    {"polygon_pin": row_with_poly.get("pin"), "polygon_source": row_with_poly.get("pin_source"), "bbox_pin": row_bbox_only.get("pin"), "bbox_source": row_bbox_only.get("pin_source")},
)

report["all_passed"] = all(c["passed"] for c in report["checks"])
Path('/app/test_reports/backend_direct_iteration_20.json').write_text(json.dumps(report, indent=2))
if not report["all_passed"]:
    sys.exit(1)