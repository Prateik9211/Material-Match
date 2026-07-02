"""Tests for India-oriented sourcing intelligence (region preference + AI prompt context).

Covers:
- /api/users/me/preferences GET/PUT
- region defaults to "India" on register
- mock analysis injects `indian_alternative` for region=India and omits it for Global
- prompt builders include India brand-context block when region=India
- validators accept the optional `indian_alternative` field
"""
import importlib
import os
import sys
import uuid

import pytest
import requests


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
server = importlib.import_module("server")
_build_analysis_prompt = server._build_analysis_prompt
_build_match_user_prompt = server._build_match_user_prompt
_validate_analysis_payload = server._validate_analysis_payload
_validate_batch_result = server._validate_batch_result

API = os.environ.get("API", "http://localhost:8001/api")


# ---------------------------------------------------------------------------
# Pure unit tests — no network
# ---------------------------------------------------------------------------
def test_analysis_prompt_includes_india_block_for_india():
    text = _build_analysis_prompt("India")
    # Brand mentions
    for brand in ["Greenlam", "Asian Paints", "Kajaria", "Hafele"]:
        assert brand in text, f"India prompt missing brand: {brand}"
    # Optional field hint
    assert "indian_alternative" in text


def test_analysis_prompt_excludes_india_block_for_global():
    text = _build_analysis_prompt("Global")
    assert "Greenlam" not in text
    assert "indian_alternative" not in text


def test_match_prompt_includes_india_block_for_india():
    text = _build_match_user_prompt("India", '{"zone":"Floor"}', "", 3)
    assert "Greenlam" in text or "Kajaria" in text
    assert "indian_alternative" in text


def test_match_prompt_excludes_india_block_for_global():
    text = _build_match_user_prompt("Global", '{"zone":"Floor"}', "", 3)
    assert "Greenlam" not in text
    assert "indian_alternative" not in text


def test_analysis_validator_accepts_indian_alternative():
    payload = {"rows": [{
        "zone": "Floor", "material_family": "wood", "material_type": "Oak",
        "color": "warm", "texture": "grain", "finish": "matt", "design_style": "scandi",
        "keywords": ["wood"], "confidence": 80,
        "indian_alternative": "Indian teak veneer + PU matt finish (Greenlam range).",
    }]}
    cleaned = _validate_analysis_payload(payload)["rows"]
    assert cleaned[0]["indian_alternative"].startswith("Indian teak")


def test_analysis_validator_handles_null_indian_alternative():
    payload = {"rows": [{
        "zone": "Floor", "material_family": "wood", "material_type": "Oak",
        "color": "warm", "texture": "grain", "finish": "matt", "design_style": "scandi",
        "keywords": ["wood"], "confidence": 80,
        "indian_alternative": None,
    }]}
    cleaned = _validate_analysis_payload(payload)["rows"]
    assert cleaned[0]["indian_alternative"] is None


def test_analysis_validator_works_without_indian_alternative():
    payload = {"rows": [{
        "zone": "Floor", "material_family": "wood", "material_type": "Oak",
        "color": "warm", "texture": "grain", "finish": "matt", "design_style": "scandi",
        "keywords": ["wood"], "confidence": 80,
    }]}
    cleaned = _validate_analysis_payload(payload)["rows"]
    assert cleaned[0]["indian_alternative"] is None


def test_match_validator_carries_indian_alternative():
    payload = {"batch_results": [{
        "candidate_index": 0,
        "candidate_type": "product_material_candidate",
        "detected_family": "wood",
        "match_percent": 78,
        "reasons": [
            {"category": "color", "text": "warm match"},
            {"category": "texture", "text": "grain"},
            {"category": "finish", "text": "matt"},
        ],
        "disqualifier": None,
        "indian_alternative": "Greenlam veneer in matt PU finish.",
    }]}
    cleaned = _validate_batch_result(payload, 1)
    assert cleaned[0]["indian_alternative"].startswith("Greenlam")


def test_match_validator_fallback_has_indian_alternative_field():
    payload = {"batch_results": [{
        "candidate_index": 0,
        "candidate_type": "product_material_candidate",
        "detected_family": "wood",
        "match_percent": 78,
        "reasons": [
            {"category": "color", "text": "warm"},
            {"category": "texture", "text": "grain"},
            {"category": "finish", "text": "matt"},
        ],
        "disqualifier": None,
        # no indian_alternative key at all
    }]}
    cleaned = _validate_batch_result(payload, 1)
    assert "indian_alternative" in cleaned[0]
    assert cleaned[0]["indian_alternative"] is None


# ---------------------------------------------------------------------------
# Integration tests — hit the live backend
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def authed_session():
    s = requests.Session()
    email = f"region_pref_{uuid.uuid4().hex[:10]}@test.com"
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "Region2026!", "name": "R"})
    r.raise_for_status()
    return s, r.json()["id"]


def test_register_defaults_to_india(authed_session):
    _, user_id = authed_session
    # Already covered by register response shape — also assert preferences endpoint
    s = authed_session[0]
    r = s.get(f"{API}/users/me/preferences")
    assert r.status_code == 200, r.text
    assert r.json()["preferred_region"] == "India"


def test_put_preferences_round_trip(authed_session):
    s, _ = authed_session
    r = s.put(f"{API}/users/me/preferences", json={"preferred_region": "Global"})
    assert r.status_code == 200
    assert r.json()["preferred_region"] == "Global"

    r = s.get(f"{API}/users/me/preferences")
    assert r.json()["preferred_region"] == "Global"

    r = s.put(f"{API}/users/me/preferences", json={"preferred_region": "India"})
    assert r.json()["preferred_region"] == "India"


def test_put_preferences_rejects_unknown_region(authed_session):
    s, _ = authed_session
    r = s.put(f"{API}/users/me/preferences", json={"preferred_region": "USA"})
    assert r.status_code == 400
    assert "must be one of" in r.json()["detail"]


def test_config_exposes_supported_regions():
    r = requests.get(f"{API}/config")
    j = r.json()
    assert j["supported_regions"] == ["India", "Global"]
    assert j["default_region"] == "India"


def test_mock_analyze_injects_indian_alternative_for_india(authed_session):
    s, _ = authed_session
    # Ensure region=India
    s.put(f"{API}/users/me/preferences", json={"preferred_region": "India"})

    pid = s.post(f"{API}/projects", json={"name": "India test"}).json()["id"]
    # Upload a tiny dummy JPEG (1x1)
    import base64 as _b64, io as _io
    pixel = _b64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD/2Q=="
    )
    s.post(f"{API}/projects/{pid}/reference",
           files={"file": ("r.jpg", _io.BytesIO(pixel), "image/jpeg")})

    r = s.post(f"{API}/projects/{pid}/mock-analyze")
    d = r.json()
    assert d.get("region") == "India"
    rows_with_ia = [row for row in d["rows"] if row.get("indian_alternative")]
    assert len(rows_with_ia) >= 1, "Expected at least one mock row with indian_alternative when region=India"

    # Switch to Global → indian_alternative should be dropped
    s.put(f"{API}/users/me/preferences", json={"preferred_region": "Global"})
    r = s.post(f"{API}/projects/{pid}/mock-analyze")
    d = r.json()
    assert d.get("region") == "Global"
    assert all(not row.get("indian_alternative") for row in d["rows"])
