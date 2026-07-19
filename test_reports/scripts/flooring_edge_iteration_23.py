#!/usr/bin/env python3
"""Direct edge check for the reported flooring -> laminate leak.

This is intentionally focused on a tile floor row whose DNA has a low-confidence
Laminate alternative, matching the root-cause class from the user report.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path("/app/backend")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import server as s  # noqa: E402

OUT = Path("/app/test_reports/flooring_edge_iteration_23.json")

row = {
    "object_type": "floor",
    "group": "Floor",
    "zone": "Floor · QA tile edge",
    "material_family": "Tile",
    "material_type": "ivory stone tile floor",
    "family_confidence": 0.5,
    "family_alternatives": ["Laminate"],
    "visual_dna": {
        "material_family": "Tile",
        "family_confidence": 0.5,
        "family_alternatives": ["Laminate"],
        "primary_color": {"hex": "#F0E9D6", "name": "ivory"},
        "finish": "Matte",
        "texture": "Smooth",
        "pattern": "plain solid",
    },
}

brain = s.materialmatch_brain(row)
matches = s._find_catalogue_matches(
    row,
    allowed_categories=brain.get("allowed_categories"),
    object_locked=brain.get("object_locked", False),
    min_overall=0,
)
bad = [m for m in matches if str(m.get("category") or "").lower() in {"laminate", "laminates", "veneer", "veneers"}]
report = {
    "row": {k: row.get(k) for k in ["object_type", "group", "zone", "material_family", "material_type", "family_confidence", "family_alternatives"]},
    "brain": {k: brain.get(k) for k in ["application_context", "allowed_categories", "object_locked", "reasoning_notes"]},
    "matches": [{"name": m.get("material_name"), "category": m.get("category"), "family": m.get("material_family"), "percent": m.get("match_percent")} for m in matches],
    "laminate_or_veneer_matches": [{"name": m.get("material_name"), "category": m.get("category"), "family": m.get("material_family"), "percent": m.get("match_percent")} for m in bad],
    "passed": len(bad) == 0,
}
OUT.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
if not report["passed"]:
    raise SystemExit(1)