"""Same-label spatial-merge unit tests — locks in the Bug B fix from
2026-02-05 round 5.

The founder reported that a single visible cabinet run in a kitchen
photo was producing 4–5 separate pins on the Analysis UI. Live
investigation on the Unsplash kitchen `photo-1600607687939-ce8a6c25118c`
showed SAM3 was returning one wide "whole cabinet run" bbox PLUS four
narrow per-door bboxes on the same row — all with pairwise IoU below
0.32 (well under the 0.70 same-class dedup threshold), so all five
survived.

`filter_detections` now runs a same-label spatial merge as its final
pass. These tests verify:
  * containment case (a big box swallowing small siblings) → 1 output
  * row-adjacency case (a chain of adjacent per-door boxes without a
    parent) → 1 output
  * eligibility gate (labels like `pillow` and `chair` are NEVER
    merged — those are legitimately individual items)
  * confidence & hull correctness — anchor is the highest-confidence
    member, bbox equals the axis-aligned hull, polygon is dropped.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from intelligence.scene_segmentation import (  # noqa: E402
    _merge_same_label_zones,
    filter_detections,
)


def _det(label, x, y, w, h, conf=0.7, polygon=None):
    return {
        "label": label,
        "confidence": conf,
        "bbox": [float(x), float(y), float(w), float(h)],
        "polygon": polygon,
    }


def test_wide_parent_swallows_child_doors():
    """Real founder-reported case reduced to a unit test: 1 wide
    cabinet-run box (206 wide) + 4 narrow per-door boxes ranged along
    the same row. All 5 must collapse to 1 merged detection whose bbox
    is the hull of the 5."""
    parent = _det("cabinet", 593, 349, 206, 60, conf=0.60)
    d0 = _det("cabinet", 551, 349, 42, 62, conf=0.75)
    d1 = _det("cabinet", 595, 349, 51, 60, conf=0.62)
    d2 = _det("cabinet", 696, 350, 67, 59, conf=0.61)
    d3 = _det("cabinet", 762, 350, 38, 59, conf=0.59)
    merged = _merge_same_label_zones([parent, d0, d1, d2, d3])
    assert len(merged) == 1, f"expected 1 merged cabinet, got {len(merged)}"
    m = merged[0]
    assert m["label"] == "cabinet"
    assert m["merged_from"] == 5
    hx, hy, hw, hh = m["bbox"]
    # Hull must span 551 → 800 horizontally, 349 → 411 vertically.
    assert hx == 551.0
    assert hy == 349.0
    assert hw == pytest_isclose(800.0 - 551.0)
    assert hh == pytest_isclose(411.0 - 349.0)
    # Anchor should be the highest-confidence member (d0 at 0.75).
    assert m["confidence"] == 0.75
    # Merged polygons are misleading — must be dropped.
    assert "polygon" not in m or m.get("polygon") is None


def test_adjacent_row_chain_without_parent():
    """No wide parent this time — just three adjacent same-label
    boxes with narrow gaps. Row-adjacency rule must merge them."""
    d0 = _det("shelf", 100, 200, 60, 20, conf=0.90)
    d1 = _det("shelf", 170, 200, 60, 20, conf=0.85)  # 10-px gap
    d2 = _det("shelf", 240, 200, 60, 20, conf=0.80)  # another 10-px gap
    merged = _merge_same_label_zones([d0, d1, d2])
    assert len(merged) == 1
    assert merged[0]["merged_from"] == 3
    hx, _, hw, _ = merged[0]["bbox"]
    assert hx == 100.0
    assert hw == 200.0  # 300 - 100


def test_ineligible_labels_never_merged():
    """`pillow`, `chair`, `artwork` etc. are individual shoppable
    items — merging them would collapse a row of pillows into one
    blob, which is wrong. Adjacent same-label pillows must stay
    separate."""
    p0 = _det("pillow", 100, 200, 60, 60, conf=0.9)
    p1 = _det("pillow", 165, 200, 60, 60, conf=0.85)  # touching
    p2 = _det("pillow", 230, 200, 60, 60, conf=0.8)
    merged = _merge_same_label_zones([p0, p1, p2])
    assert len(merged) == 3, "pillows must remain separate detections"
    for m in merged:
        assert "merged_from" not in m


def test_far_apart_same_label_left_alone():
    """Two cabinets on OPPOSITE sides of a kitchen (no shared row,
    no containment) must NOT be merged — they're two legitimate
    zones, not one."""
    left = _det("cabinet", 0, 300, 200, 400, conf=0.9)
    right = _det("cabinet", 900, 300, 200, 400, conf=0.85)  # far right
    merged = _merge_same_label_zones([left, right])
    assert len(merged) == 2


def test_full_pipeline_via_filter_detections():
    """End-to-end: pass raw SAM3-shaped detections through
    `filter_detections` and verify the merge fires at the end."""
    dets = [
        _det("cabinet", 593, 349, 206, 60, conf=0.60),
        _det("cabinet", 551, 349, 42, 62, conf=0.75),
        _det("cabinet", 595, 349, 51, 60, conf=0.62),
        # Unrelated wall detection — must pass through unchanged.
        _det("wall", 0, 0, 1400, 300, conf=0.9),
        # Two pillows on the bed — must NOT merge.
        _det("pillow", 500, 400, 50, 50, conf=0.9),
        _det("pillow", 560, 400, 50, 50, conf=0.85),
    ]
    out = filter_detections(dets, image_w=1400, image_h=800,
                            min_confidence=0.3, min_area_frac=0)
    labels = sorted([d["label"] for d in out])
    assert labels == ["cabinet", "pillow", "pillow", "wall"]
    # The one cabinet must be the merged hull.
    cab = [d for d in out if d["label"] == "cabinet"][0]
    assert cab.get("merged_from", 1) >= 2


# Small helper because the merge rounds mildly through float math.
def pytest_isclose(v):
    class _Approx:
        def __eq__(self, other):
            return abs(float(other) - float(v)) < 1e-6
        def __repr__(self):
            return f"~{v}"
    return _Approx()
