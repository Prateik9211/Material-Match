#!/usr/bin/env python3
"""Focused backend verification for iteration 21 retest.

Checks only the founder-reported family-gating, shortlist field, and pin fixes.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path("/app/backend")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import server as s  # noqa: E402
from intelligence.retrieval import attribute_similarity  # noqa: E402

OUT = Path("/app/test_reports/backend_direct_iteration_21.json")
report = {"checks": []}


def check(name, passed, details=None):
    report["checks"].append({"name": name, "passed": bool(passed), "details": details or {}})
    print(f"{'PASS' if passed else 'FAIL'}: {name} {details or ''}")


# Follow-up fix #1: unknown/Other architectural walls lock to Paints only.
unknown_results = []
for fam in (None, "", "Other", "Unknown"):
    row = {
        "object_type": "wall",
        "material_family": fam,
        "material_type": "smooth matte wall surface",
        "material_confidence": 60,
        "family_confidence": 0.45,
        "family_alternatives": ["Laminate"],
    }
    brain = s.materialmatch_brain(row)
    unknown_results.append({"family": fam, "allowed": brain.get("allowed_categories"), "locked": brain.get("object_locked")})
check(
    "wall/ceiling unknown-family defaults to Paints only with object_locked=True",
    all(r["allowed"] == ["Paints"] and r["locked"] is True for r in unknown_results),
    {"results": unknown_results},
)


# Self-consistency widen from explicit material_type must still fire.
self_brain = s.materialmatch_brain({
    "object_type": "wall",
    "material_family": None,
    "material_type": "warm oak laminate panel",
    "material_confidence": 65,
})
check(
    "material_type self-consistency widen still allows Laminates when text says laminate",
    self_brain.get("object_locked") is True
    and "Paints" in self_brain.get("allowed_categories", [])
    and "Laminates" in self_brain.get("allowed_categories", []),
    {"brain": {k: self_brain.get(k) for k in ["object_locked", "allowed_categories"]}},
)


# Object locked _find_catalogue_matches must not widen into Laminates.
wall_row = {
    "object_type": "wall",
    "material_family": "Paint",
    "material_type": "matte wall paint",
    "family_confidence": 0.5,
    "family_alternatives": ["Laminate", "Veneer"],
    "visual_dna": {"material_family": "Paint", "family_confidence": 0.5, "family_alternatives": ["Laminate", "Veneer"]},
    "color_hex": "#F1EBE0",
}

captured_locked = {}


def spy_locked(query_dna, ref_hashes, items, top_k=8):
    captured_locked["categories"] = sorted({(it.get("category") or "") for it in items})
    return {"candidates": [], "meta": {}}


with patch("intelligence.pipeline.retrieve_matches", side_effect=spy_locked):
    s._find_catalogue_matches(dict(wall_row), allowed_categories=["Paints"], object_locked=True)
check(
    "_find_catalogue_matches skips widen when object_locked=True",
    "Laminates" not in captured_locked.get("categories", []) and "Paints" in captured_locked.get("categories", []),
    {"retrieval_categories": captured_locked.get("categories", [])},
)

captured_unlocked = {}


def spy_unlocked(query_dna, ref_hashes, items, top_k=8):
    captured_unlocked["categories"] = sorted({(it.get("category") or "") for it in items})
    return {"candidates": [], "meta": {}}


with patch("intelligence.pipeline.retrieve_matches", side_effect=spy_unlocked):
    s._find_catalogue_matches(dict(wall_row), allowed_categories=["Paints"], object_locked=False)
check(
    "_find_catalogue_matches still widens when object_locked=False",
    "Laminates" in captured_unlocked.get("categories", []) and "Paints" in captured_unlocked.get("categories", []),
    {"retrieval_categories": captured_unlocked.get("categories", [])},
)


# Real unmocked catalogue match check for the exact previous failing edge: Other wall + laminate alt.
edge = {
    "object_type": "wall",
    "material_family": "Other",
    "material_type": "smooth surface",
    "material_confidence": 60,
    "family_confidence": 0.45,
    "family_alternatives": ["Laminate"],
    "visual_dna": {
        "material_family": "Other",
        "family_confidence": 0.45,
        "family_alternatives": ["Laminate"],
        "primary_color": {"hex": "#F1EBE0", "name": "warm white"},
        "finish": "matte",
        "texture": "smooth",
        "pattern": "plain solid",
    },
    "color_hex": "#F1EBE0",
}
edge_brain = s.materialmatch_brain(edge)
edge_matches = s._find_catalogue_matches(edge, allowed_categories=edge_brain["allowed_categories"], object_locked=edge_brain["object_locked"])
bad_edge = [m for m in edge_matches if str(m.get("category") or "").lower() not in {"paint", "paints"}]
check(
    "unmocked Other wall catalogue search returns only Paints",
    len(edge_matches) > 0 and not bad_edge,
    {"allowed": edge_brain.get("allowed_categories"), "matches": [{"name": m.get("material_name"), "category": m.get("category"), "percent": m.get("match_percent")} for m in edge_matches[:5]], "bad": bad_edge},
)


attr = attribute_similarity(
    {"material_family": "paint", "family_confidence": 0.5, "family_alternatives": ["laminate"], "primary_color": {"hex": "#F1EBE0"}},
    {"material_family": "laminate", "primary_color": {"hex": "#F1EBE0"}},
)
check("attribute_similarity alt-family penalty is 0.7", attr.get("family") == 0.7, {"attribute_similarity": attr})


row_poly = s._dna_to_row(
    {"material_family": "Fabric", "surface_type": "rug", "primary_color": {"name": "beige", "hex": "#d0c0aa"}},
    object_label="rug",
    object_confidence=0.9,
    index=0,
    bbox=[0, 800, 1000, 200],
    polygon=[{"x": 100, "y": 820}, {"x": 300, "y": 820}, {"x": 350, "y": 980}, {"x": 250, "y": 980}],
    source="dna",
    image_size=(1000, 1000),
)
check(
    "_dna_to_row prefers polygon centroid over bbox center",
    row_poly.get("pin_source") == "scene_polygon_centroid" and row_poly.get("pin") == {"x": 25.0, "y": 90.0},
    {"pin": row_poly.get("pin"), "pin_source": row_poly.get("pin_source")},
)

fields = s.ShortlistItemCreate.model_fields
check(
    "ShortlistItemCreate includes swatch_crop_b64/color_hex/material_code",
    all(f in fields for f in ("swatch_crop_b64", "color_hex", "material_code")),
    {"present": [f for f in ("swatch_crop_b64", "color_hex", "material_code") if f in fields]},
)

report["all_passed"] = all(c["passed"] for c in report["checks"])
OUT.write_text(json.dumps(report, indent=2))
if not report["all_passed"]:
    raise SystemExit(1)