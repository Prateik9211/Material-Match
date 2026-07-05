"""Sprint 4 — Demo endpoints + no-regression smoke tests"""
import os, requests, pytest

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://design-match-ai.preview.emergentagent.com').rstrip('/')
ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASSWORD = "MaterialAdmin2026!"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


# --- Public demo endpoints (no auth) ---
class TestDemoEndpoints:
    def test_demo_project_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/demo/project")
        assert r.status_code == 200
        d = r.json()
        # v1.0 RC1 — demo re-seeded to Earthen Serenity Living living-room
        assert d.get("name") == "Earthen Serenity Living \u2014 Demo"
        assert d.get("is_demo") is True
        rows = (d.get("mock_analysis") or {}).get("rows", [])
        assert len(rows) == 10, f"expected 10 curated zones, got {len(rows)}"
        # 9 zones should each have exactly 3 matches; zone 10 (Foliage) 0 matches
        counts = [len(r.get("catalogue_matches", [])) for r in rows]
        assert counts.count(3) == 9 and counts.count(0) == 1, f"match counts={counts}"
        # No fabricated MM-DEMO-* codes anywhere
        blob = str(d)
        assert "MM-DEMO" not in blob, "Fabricated MM-DEMO-* codes present"
        assert "password_hash" not in blob

    def test_demo_reference_image_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/demo/reference-image")
        assert r.status_code == 200
        d = r.json()
        assert d.get("data_url", "").startswith("data:image/jpeg;base64,")

    def test_demo_project_read_only(self):
        # try to mutate demo via public PATCH-like op — expect 401/403/404
        r = requests.get(f"{BASE_URL}/api/demo/project")
        pid = r.json().get("id") or r.json().get("_id") or r.json().get("project_id")
        if pid:
            # attempt to hit standard project endpoint without auth
            m = requests.delete(f"{BASE_URL}/api/projects/{pid}")
            assert m.status_code in (401, 403, 404, 405)


# --- Auth smoke (regression) ---
class TestAuthRegression:
    def test_admin_login(self, s):
        r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        assert "access_token" in s.cookies or r.json().get("access_token") or r.json().get("user")

    def test_admin_me(self, s):
        r = s.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200
        assert r.json().get("email") == ADMIN_EMAIL

    def test_admin_affiliates_list(self, s):
        r = s.get(f"{BASE_URL}/api/admin/affiliates")
        assert r.status_code == 200
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data.get("affiliates", []))
        assert len(items) >= 10, f"expected >=10 affiliates, got {len(items)}"


# --- Public share for demo room ---
class TestDemoPublicRoom:
    def test_public_demo_room_slug(self):
        r = requests.get(f"{BASE_URL}/api/public/rooms/materialmatch-demo")
        assert r.status_code == 200, r.text
        d = r.json()
        # no leaks
        blob = str(d)
        assert "password_hash" not in blob
        assert "user_id" not in d  # top-level
