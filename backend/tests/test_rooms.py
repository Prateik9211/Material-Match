"""Sprint 3 tests: Concept Presentation Workspace (rooms).

Covers:
  - Constants + helpers (kind_field, slug generator, out serializer)
  - RoomCreate/RoomUpdate schema behaviour
"""
import pytest
import re
from server import (
    ROOM_TYPES,
    IMAGE_KINDS,
    MAX_IMAGES_PER_KIND,
    _kind_field,
    _make_slug,
    _room_out,
    RoomCreate,
    RoomUpdate,
)


def test_room_type_enum_covers_common_indian_home_types():
    for k in ("living", "bedroom", "kitchen", "bath", "dining", "office"):
        assert k in ROOM_TYPES
    assert "custom" in ROOM_TYPES


def test_image_kinds_are_the_four_story_layers():
    # Sprint 5A adds 'final_render' for the merged Design Direction section.
    assert set(IMAGE_KINDS) == {"current_site", "moodboard", "reference", "final_render"}


def test_max_images_per_kind_is_capped_sensibly():
    assert 4 <= MAX_IMAGES_PER_KIND <= 24


def test_kind_field_maps_to_mongo_field():
    assert _kind_field("current_site") == "current_site_photos"
    assert _kind_field("moodboard") == "moodboards"
    assert _kind_field("reference") == "reference_images"


def test_kind_field_invalid_raises():
    with pytest.raises(KeyError):
        _kind_field("random_kind")


def test_make_slug_is_short_and_url_safe():
    for _ in range(20):
        s = _make_slug()
        assert 6 <= len(s) <= 14
        # Should be url-safe (lowercase alnum only after our stripping)
        assert re.fullmatch(r"[a-z0-9]+", s), f"slug not url-safe: {s}"


def test_make_slug_is_random():
    slugs = {_make_slug() for _ in range(50)}
    assert len(slugs) > 40  # extremely unlikely to collide often


def test_room_out_hides_image_bytes_and_converts_id():
    from bson import ObjectId
    oid = ObjectId()
    doc = {
        "_id": oid,
        "name": "Living",
        "current_site_photos": [{"id": "a", "mime": "image/jpeg", "b64": "AAAA" * 100}],
        "moodboards": [],
        "reference_images": [{"id": "r1", "mime": "image/png", "b64": "BBBB"}],
        "share_slug": "xyz",
    }
    out = _room_out(doc)
    assert out["id"] == str(oid)
    assert "_id" not in out
    # No base64 bytes in the response
    assert "b64" not in out["current_site_photos"][0]
    assert out["current_site_photos"][0] == {"id": "a", "mime": "image/jpeg"}
    assert out["reference_images"] == [{"id": "r1", "mime": "image/png"}]
    assert out["moodboards"] == []


def test_room_create_defaults_room_type():
    r = RoomCreate(name="Master Bedroom")
    assert r.name == "Master Bedroom"
    assert r.room_type == "custom"


def test_room_create_requires_name():
    with pytest.raises(Exception):
        RoomCreate(name="")


def test_room_update_allows_partial_fields():
    u = RoomUpdate(concept_overview="Draft text")
    assert u.concept_overview == "Draft text"
    assert u.name is None
    assert u.pinned_material_row_ids is None


def test_room_update_accepts_pin_lists():
    u = RoomUpdate(pinned_material_row_ids=["Ceiling", "Wall Feature"],
                   pinned_product_ids=["product_1", "product_2"])
    assert u.pinned_material_row_ids == ["Ceiling", "Wall Feature"]
    assert u.pinned_product_ids == ["product_1", "product_2"]
