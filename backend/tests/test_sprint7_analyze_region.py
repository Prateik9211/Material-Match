"""Sprint 7 backend tests: POST /api/projects/{id}/analyze-region.

Covers unauth 401, foreign project 404, oversized crop 413, and 200 with rows
on valid crop (deterministic mock when ENABLE_REAL_ANALYSIS/EMERGENT_LLM_KEY off).
"""
import os
import uuid
import base64
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://design-match-ai.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASS = "MaterialAdmin2026!"

# 1x1 PNG (transparent) then base64 padded to a small valid crop
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVQYV2NgYAAAAAMAAWgmWQ0AAAAASUVORK5CYII="
)
# The endpoint checks min_length=32 on crop_b64. That tiny PNG b64 is >32 chars, good.

@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
               timeout=20)
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="module")
def second_user_session():
    email = f"TEST_sprint7_{uuid.uuid4().hex[:8]}@example.com"
    s = requests.Session()
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "TestPass2026!", "name": "Sprint7 Tester"},
               timeout=20)
    assert r.status_code in (200, 201), r.text
    return s


@pytest.fixture(scope="module")
def project_id(admin_session):
    r = admin_session.post(f"{API}/projects", json={
        "name": "TEST_Sprint7_Region",
        "room_type": "Living Room",
        "budget_range": "Standard",
    }, timeout=20)
    assert r.status_code in (200, 201), r.text
    pid = r.json().get("id") or r.json().get("_id")
    assert pid
    yield pid
    try:
        admin_session.delete(f"{API}/projects/{pid}", timeout=15)
    except Exception:
        pass


class TestAnalyzeRegion:
    def test_unauth_returns_401(self, project_id):
        r = requests.post(f"{API}/projects/{project_id}/analyze-region",
                          json={"crop_b64": TINY_PNG_B64, "note": "x"}, timeout=20)
        assert r.status_code in (401, 403)

    def test_foreign_project_returns_404(self, second_user_session, project_id):
        r = second_user_session.post(f"{API}/projects/{project_id}/analyze-region",
                                     json={"crop_b64": TINY_PNG_B64, "note": "x"}, timeout=20)
        assert r.status_code == 404

    def test_oversized_crop_returns_413(self, admin_session, project_id):
        # Build a base64 that decodes to >6MB (default LLM_ANALYSIS_REF_IMAGE_MAX_BYTES).
        # Use ~9MB of A's b64-encoded (~12MB char). That's a big string but fine for a single request.
        raw = b"A" * (9 * 1024 * 1024)
        big_b64 = base64.b64encode(raw).decode()
        r = admin_session.post(f"{API}/projects/{project_id}/analyze-region",
                               json={"crop_b64": big_b64, "note": "big"}, timeout=60)
        assert r.status_code == 413, f"expected 413, got {r.status_code}: {r.text[:200]}"

    def test_valid_crop_returns_rows(self, admin_session, project_id):
        r = admin_session.post(f"{API}/projects/{project_id}/analyze-region",
                               json={"crop_b64": TINY_PNG_B64, "note": "user selection"}, timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "rows" in body and isinstance(body["rows"], list)
        assert len(body["rows"]) >= 1
        row = body["rows"][0]
        # each row has zone + material_type per contract
        assert "zone" in row
        assert "material_type" in row or "material_family" in row
        assert body.get("ephemeral") is True
        assert "summary" in body

    def test_valid_crop_with_data_url_prefix_accepted(self, admin_session, project_id):
        data_url = f"data:image/png;base64,{TINY_PNG_B64}"
        r = admin_session.post(f"{API}/projects/{project_id}/analyze-region",
                               json={"crop_b64": data_url}, timeout=60)
        assert r.status_code == 200, r.text
        assert r.json().get("ephemeral") is True

    def test_short_crop_returns_422(self, admin_session, project_id):
        # crop_b64 has min_length=32
        r = admin_session.post(f"{API}/projects/{project_id}/analyze-region",
                               json={"crop_b64": "abc"}, timeout=15)
        assert r.status_code == 422
