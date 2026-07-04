"""Sprint 6 backend tests: Shortlist CRUD + Material Library (global/my)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://design-match-ai.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASS = "MaterialAdmin2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
               timeout=20)
    assert r.status_code == 200
    return s


@pytest.fixture(scope="module")
def second_user_session():
    """A second unique user for cross-user isolation test."""
    import uuid
    email = f"TEST_sprint6_{uuid.uuid4().hex[:8]}@example.com"
    s = requests.Session()
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "TestPass2026!", "name": "Sprint6 Tester"},
               timeout=20)
    assert r.status_code in (200, 201), r.text
    return s


@pytest.fixture(scope="module")
def project_id(admin_session):
    r = admin_session.post(f"{API}/projects", json={
        "name": "TEST_Sprint6_Shortlist",
        "room_type": "Living Room",
        "budget_range": "Standard",
    }, timeout=20)
    assert r.status_code in (200, 201), r.text
    pid = r.json().get("id") or r.json().get("_id")
    assert pid
    yield pid
    # cleanup
    try:
        admin_session.delete(f"{API}/projects/{pid}", timeout=15)
    except Exception:
        pass


# -------- Shortlist CRUD --------
class TestShortlist:
    def test_unauth_shortlist_get_returns_401(self, project_id):
        r = requests.get(f"{API}/projects/{project_id}/shortlist", timeout=15)
        assert r.status_code in (401, 403)

    def test_empty_shortlist_initially(self, admin_session, project_id):
        r = admin_session.get(f"{API}/projects/{project_id}/shortlist", timeout=15)
        assert r.status_code == 200
        assert r.json().get("items") == []

    def test_add_shortlist_item(self, admin_session, project_id):
        payload = {
            "name": "TEST_Warm Oak Veneer",
            "source_type": "product",
            "source": "Century Ply",
            "match_percent": 82,
            "zone": "living_room",
            "category": "Wood",
            "notes": "Match for feature wall",
            "external_url": "https://example.com/oak",
        }
        r = admin_session.post(f"{API}/projects/{project_id}/shortlist", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        item = r.json()
        assert item["id"] and isinstance(item["id"], str)
        assert item["name"] == payload["name"]
        assert item["source_type"] == "product"
        assert item["match_percent"] == 82
        # GET should now return this item
        g = admin_session.get(f"{API}/projects/{project_id}/shortlist", timeout=15)
        assert g.status_code == 200
        ids = [i["id"] for i in g.json()["items"]]
        assert item["id"] in ids

    def test_invalid_source_type_400(self, admin_session, project_id):
        r = admin_session.post(f"{API}/projects/{project_id}/shortlist", json={
            "name": "bad", "source_type": "invalid_type",
        }, timeout=15)
        assert r.status_code == 400

    def test_cross_user_access_returns_404(self, second_user_session, project_id):
        # second user tries to access admin's project shortlist
        r = second_user_session.get(f"{API}/projects/{project_id}/shortlist", timeout=15)
        assert r.status_code == 404
        r2 = second_user_session.post(f"{API}/projects/{project_id}/shortlist", json={
            "name": "hack", "source_type": "product",
        }, timeout=15)
        assert r2.status_code == 404

    def test_delete_shortlist_item(self, admin_session, project_id):
        # add one to delete
        r = admin_session.post(f"{API}/projects/{project_id}/shortlist", json={
            "name": "TEST_ToDelete", "source_type": "custom",
        }, timeout=15)
        assert r.status_code == 200
        item_id = r.json()["id"]
        d = admin_session.delete(f"{API}/projects/{project_id}/shortlist/{item_id}", timeout=15)
        assert d.status_code == 200
        assert d.json().get("ok") is True
        # verify removal
        g = admin_session.get(f"{API}/projects/{project_id}/shortlist", timeout=15)
        ids = [i["id"] for i in g.json()["items"]]
        assert item_id not in ids


# -------- Material Library --------
class TestLibrary:
    def test_unauth_library_global_returns_401(self):
        r = requests.get(f"{API}/library/global", timeout=15)
        assert r.status_code in (401, 403)

    def test_unauth_library_my_returns_401(self):
        r = requests.get(f"{API}/library/my", timeout=15)
        assert r.status_code in (401, 403)

    def test_library_global_returns_4_items_status_beta(self, admin_session):
        r = admin_session.get(f"{API}/library/global", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "beta"
        items = body["items"]
        assert len(items) == 4
        brands = {i["brand"] for i in items}
        # 4 seeded Indian brands
        assert "Asian Paints" in brands
        assert "Kajaria" in brands
        assert "Century Ply" in brands
        # Häfele India
        assert any("Häfele" in b or "Hafele" in b for b in brands)
        for it in items:
            assert it["region"] == "India"
            assert it["status"] == "coming_soon"
            assert it["id"].startswith("global-")

    def test_library_my_returns_items_shape(self, admin_session):
        r = admin_session.get(f"{API}/library/my", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["reuse_status"] == "coming_soon"
        assert isinstance(body["items"], list)
        # each item should have expected shape when present
        for it in body["items"]:
            assert "name" in it and "usage_count" in it
            assert isinstance(it["usage_count"], int)
