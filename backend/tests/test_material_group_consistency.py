"""Unit tests for the same-material grouping + anchor-broadcast pipeline.

Founder requirement (2026-02-08): rows that resolve to the SAME physical
material (same family + similar color + non-conflicting finish/gloss)
MUST end up sharing the IDENTICAL catalogue match result — computed once
per group on the elected anchor row, then broadcast to every sibling.
Divergent results for the same material are the core bug we're fixing.
"""
import os
import sys
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_database")

from server import (  # noqa: E402
    _annotate_material_groups,
    _broadcast_anchor_match_to_group,
    _cluster_rows_by_material,
    _material_signature_matches,
    _GROUP_BROADCAST_FIELDS,
)


def _row(**over) -> dict:
    base = {
        "material_family": "Laminate",
        "color": "Blue",
        "color_hex": "#3A5F9B",
        "finish": "Glossy",
        "gloss_level": "high",
        "texture": "smooth",
        "confidence": 70,
        "scene_bbox": [0, 0, 100, 100],
        "scene_polygon": None,
        "pin": {"x": 10, "y": 10},
        "zone": "Cabinet · region 1",
    }
    base.update(over)
    return base


def test_signature_matches_same_material():
    a = _row()
    b = _row(color_hex="#3B5F9E")  # 3-pt RGB shift → sim >> 85
    assert _material_signature_matches(a, b) is True


def test_signature_rejects_family_mismatch():
    a = _row(material_family="Laminate")
    b = _row(material_family="Paint")
    assert _material_signature_matches(a, b) is False


def test_signature_rejects_color_mismatch():
    a = _row(color_hex="#3A5F9B")  # blue
    b = _row(color_hex="#F0EEE8")  # cream
    assert _material_signature_matches(a, b) is False


def test_signature_rejects_finish_conflict():
    a = _row(gloss_level="high")
    b = _row(gloss_level="matte")
    assert _material_signature_matches(a, b) is False


def test_signature_tolerates_missing_finish():
    a = _row(gloss_level="high", finish="Glossy")
    b = _row(gloss_level="", finish="")
    assert _material_signature_matches(a, b) is True


def test_clustering_five_cabinet_doors_collapse_to_one_group():
    # Five cabinet-door detections — same blue laminate, slight color drift.
    hex_variants = ["#3A5F9B", "#3B609C", "#395E9A", "#3D619E", "#3A5E9B"]
    rows = [_row(color_hex=h, confidence=60 + i, zone=f"Cabinet · region {i+1}")
            for i, h in enumerate(hex_variants)]
    clusters = _cluster_rows_by_material(rows)
    assert len(clusters) == 1
    assert sorted(clusters[0]) == [0, 1, 2, 3, 4]


def test_clustering_mixed_materials_stay_separate():
    rows = [
        _row(material_family="Laminate", color_hex="#3A5F9B"),  # blue laminate
        _row(material_family="Laminate", color_hex="#3A5F9B"),  # blue laminate again
        _row(material_family="Paint",    color_hex="#F0EEE8"),  # white paint
        _row(material_family="Wood",     color_hex="#8B5A2B"),  # walnut
    ]
    clusters = _cluster_rows_by_material(rows)
    # 3 groups: {0,1}, {2}, {3}
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 1, 2]


def test_annotate_elects_highest_confidence_anchor():
    rows = [
        _row(confidence=50, scene_bbox=[0, 0, 10, 10]),
        _row(confidence=90, scene_bbox=[0, 0, 20, 20]),  # highest conf → anchor
        _row(confidence=70, scene_bbox=[0, 0, 30, 30]),
    ]
    _annotate_material_groups(rows)
    assert [r["is_group_anchor"] for r in rows] == [False, True, False]
    assert all(r["material_group_id"] == 0 for r in rows)
    assert all(r["group_member_count"] == 3 for r in rows)


def test_annotate_bbox_area_tiebreak_when_confidence_equal():
    rows = [
        _row(confidence=80, scene_bbox=[0, 0, 10, 10]),
        _row(confidence=80, scene_bbox=[0, 0, 30, 30]),   # bigger → anchor
        _row(confidence=80, scene_bbox=[0, 0, 20, 20]),
    ]
    _annotate_material_groups(rows)
    anchors = [i for i, r in enumerate(rows) if r["is_group_anchor"]]
    assert anchors == [1]


def test_broadcast_replicates_anchor_match_to_siblings():
    # Set up a 3-row group; only the anchor holds a "real" catalogue match.
    rows = [
        _row(confidence=50, zone="sibling A"),
        _row(confidence=90, zone="anchor"),
        _row(confidence=70, zone="sibling B"),
    ]
    _annotate_material_groups(rows)
    anchor = next(r for r in rows if r["is_group_anchor"])
    anchor_match_payload = {
        "catalogue_matches": [
            {"id": "REC_BLUE_SHIMMER", "material_name": "Blue Shimmer",
             "match_percent": 88, "swatch_crop_b64": "AAAA"},
        ],
        "match_buckets": {"strong": ["REC_BLUE_SHIMMER"], "possible": []},
        "match_state": {"no_confident_match": False},
        "rerank": {"ran": True, "accepted": 1},
        "alternative_systems": [{"name": "HPL Laminate"}],
        "searched_categories": ["Laminates"],
        "searched_libraries": ["admin"],
        "excluded_libraries": [],
    }
    for k, v in anchor_match_payload.items():
        anchor[k] = v

    _broadcast_anchor_match_to_group(rows)

    for r in rows:
        # Every field must be equal to the anchor's copy.
        for f in _GROUP_BROADCAST_FIELDS:
            assert r[f] == anchor[f], (
                f"Row {r['zone']!r} field {f!r} diverges from anchor. "
                f"row={r[f]!r} anchor={anchor[f]!r}"
            )
        # Top pick must reference the exact same catalogue record.
        assert r["catalogue_matches"][0]["id"] == "REC_BLUE_SHIMMER"
        assert r["catalogue_matches"][0]["match_percent"] == 88
    # And siblings must be flagged so audit is trivial.
    for r in rows:
        if not r["is_group_anchor"]:
            assert r.get("group_result_source") == "broadcast_from_anchor"


def test_broadcast_deepcopies_so_sibling_mutation_cannot_corrupt_anchor():
    rows = [_row(confidence=90), _row(confidence=50)]
    _annotate_material_groups(rows)
    anchor = next(r for r in rows if r["is_group_anchor"])
    sibling = next(r for r in rows if not r["is_group_anchor"])
    anchor["catalogue_matches"] = [{"id": "X", "match_percent": 80}]
    _broadcast_anchor_match_to_group(rows)
    sibling["catalogue_matches"][0]["match_percent"] = 1
    assert anchor["catalogue_matches"][0]["match_percent"] == 80


def test_broadcast_noop_on_singleton_groups():
    rows = [_row(material_family="Laminate", color_hex="#3A5F9B"),
            _row(material_family="Paint", color_hex="#F0EEE8")]
    _annotate_material_groups(rows)
    # Every row is its own anchor.
    assert all(r["is_group_anchor"] for r in rows)
    # Broadcast is safe to call and leaves rows unchanged (no siblings).
    snapshot = deepcopy(rows)
    _broadcast_anchor_match_to_group(rows)
    assert rows == snapshot


def test_broadcast_no_op_on_llm_only_fallback_rows_without_group_id():
    # `run_real_analysis` rows never get material_group_id — broadcast must
    # skip them safely (they behave as pre-grouping code always did).
    rows = [{"material_family": "Wood", "color_hex": "#8B5A2B"}]
    # No annotation call — simulates the LLM-only fallback path.
    _broadcast_anchor_match_to_group(rows)  # should not raise
    assert "material_group_id" not in rows[0]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
