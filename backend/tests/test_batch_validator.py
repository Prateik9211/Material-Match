"""Unit tests for _validate_batch_result resilience.

Locks in behaviour fixed on 2026-02: a single mangled item (e.g. a room-scene
candidate the LLM forgot to give 3 reasons to) must NOT cause the entire batch
to be dropped. Valid product candidates in the same batch must survive.
"""
import importlib
import sys
import os
import pytest

# Make backend importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
server = importlib.import_module("server")
_validate_batch_result = server._validate_batch_result


def _good_entry(idx, ctype="product_material_candidate", family="wood", pct=72):
    return {
        "candidate_index": idx,
        "candidate_type": ctype,
        "detected_family": family,
        "match_percent": pct,
        "reasons": [
            {"category": "color", "text": "matches"},
            {"category": "texture", "text": "matches"},
            {"category": "finish", "text": "matches"},
        ],
        "disqualifier": None,
    }


def test_happy_path_all_valid():
    payload = {"batch_results": [_good_entry(0), _good_entry(1), _good_entry(2)]}
    out = _validate_batch_result(payload, 3)
    assert len(out) == 3
    assert out[0]["match_percent"] == 72
    assert all(o["candidate_type"] == "product_material_candidate" for o in out)


def test_room_scene_with_zero_reasons_does_not_kill_batch():
    payload = {"batch_results": [
        _good_entry(0),  # valid product
        {  # room scene with 0 reasons (the bug)
            "candidate_index": 1,
            "candidate_type": "room_scene_or_lifestyle",
            "detected_family": "other",
            "match_percent": 5,
            "reasons": [],
            "disqualifier": "Room scene, not a product photo",
        },
        _good_entry(2),  # valid product
    ]}
    out = _validate_batch_result(payload, 3)
    assert len(out) == 3
    assert out[0]["candidate_type"] == "product_material_candidate"
    assert out[1]["candidate_type"] == "room_scene_or_lifestyle"
    assert out[1]["reasons"] == []  # allowed for room scenes
    assert out[2]["candidate_type"] == "product_material_candidate"


def test_room_scene_with_one_reason_survives():
    payload = {"batch_results": [
        _good_entry(0),
        {
            "candidate_index": 1,
            "candidate_type": "room_scene_or_lifestyle",
            "detected_family": "other",
            "match_percent": 10,
            "reasons": [{"category": "color", "text": "full kitchen scene"}],
            "disqualifier": None,
        },
    ]}
    out = _validate_batch_result(payload, 2)
    assert len(out) == 2
    assert len(out[1]["reasons"]) == 1


def test_partial_failure_fills_zero_fallback():
    payload = {"batch_results": [
        _good_entry(0),
        {  # malformed: missing match_percent
            "candidate_index": 1,
            "candidate_type": "product_material_candidate",
            "detected_family": "wood",
            "reasons": [{"category": "color", "text": "x"}],
        },
        _good_entry(2),
    ]}
    out = _validate_batch_result(payload, 3)
    assert len(out) == 3
    assert out[0]["match_percent"] == 72
    assert out[1]["match_percent"] == 0  # zero fallback
    assert out[1]["candidate_type"] == "unclear"
    assert out[2]["match_percent"] == 72


def test_unknown_detected_family_is_coerced_to_other():
    payload = {"batch_results": [{
        "candidate_index": 0,
        "candidate_type": "unclear",
        "detected_family": "spaceship",  # invalid
        "match_percent": 30,
        "reasons": [],
        "disqualifier": None,
    }]}
    out = _validate_batch_result(payload, 1)
    assert out[0]["detected_family"] == "other"


def test_extra_reasons_are_truncated_to_three():
    payload = {"batch_results": [{
        "candidate_index": 0,
        "candidate_type": "product_material_candidate",
        "detected_family": "wood",
        "match_percent": 80,
        "reasons": [
            {"category": "color", "text": "a"},
            {"category": "texture", "text": "b"},
            {"category": "finish", "text": "c"},
            {"category": "style", "text": "d"},  # 4th — should be dropped
        ],
        "disqualifier": None,
    }]}
    out = _validate_batch_result(payload, 1)
    assert len(out[0]["reasons"]) == 3


def test_product_candidate_with_zero_reasons_is_dropped_to_fallback():
    # A product_material_candidate MUST come with at least one usable reason,
    # otherwise it is unparseable — drop to zero fallback instead of trusting it.
    payload = {"batch_results": [
        _good_entry(0),
        {
            "candidate_index": 1,
            "candidate_type": "product_material_candidate",
            "detected_family": "wood",
            "match_percent": 80,
            "reasons": [],
            "disqualifier": None,
        },
    ]}
    out = _validate_batch_result(payload, 2)
    assert out[0]["match_percent"] == 72
    assert out[1]["match_percent"] == 0
    assert out[1]["candidate_type"] == "unclear"


def test_missing_envelope_raises():
    with pytest.raises(ValueError):
        _validate_batch_result({}, 3)
    with pytest.raises(ValueError):
        _validate_batch_result({"batch_results": "nope"}, 3)


def test_all_items_fail_raises():
    payload = {"batch_results": [
        {"candidate_index": 99, "candidate_type": "x", "detected_family": "y", "match_percent": "bad", "reasons": []},
    ]}
    with pytest.raises(ValueError):
        _validate_batch_result(payload, 1)
