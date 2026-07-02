"""Sprint 3 integration tests: rooms + concept + share + public endpoints.

Hits the live backend via REACT_APP_BACKEND_URL. Cleans up rooms it creates.
"""
import os
import io
import base64
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://design-match-ai.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = ("admin@materialmatch.ai", "MaterialAdmin2026!")
DESIGNER = ("designer@test.com", "Designer2026!", "Test Designer")

# 1x1 PNG (67 bytes)
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        return None
    return r.json().get("access_token") or r.json().get("token")


def _register_if_needed(email, password, name):
    tok = _login(email, password)
    if tok:
        return tok
    requests.post(f"{API}/auth/register", json={"email": email, "password": password, "name": name})
    return _login(email, password)


@pytest.fixture(scope="module")
def admin_token():
    tok = _login(*ADMIN)
    if not tok:
        pytest.skip("Admin login failed")
    return tok


@pytest.fixture(scope="module")
def designer_token():
    tok = _register_if_needed(*DESIGNER)
    if not tok:
        pytest.skip("Designer login failed")
    return tok


@pytest.fixture(scope="module")
def admin_project(admin_token):
    """Reuse first admin project or create one."""
    h = {"Authorization": f"Bearer {admin_token}"}
    r = requests.get(f"{API}/projects", headers=h)
    assert r.status_code == 200
    projects = r.json()
    if projects:
        return projects[0]["id"]
    r = requests.post(f"{API}/projects", headers=h, json={"name": "TEST_Sprint3", "client_name": "TEST Client"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.fixture
def created_room(admin_token, admin_project):
    """Create a room and clean up after."""
    h = {"Authorization": f"Bearer {admin_token}"}
    r = requests.post(f"{API}/projects/{admin_project}/rooms", headers=h,
                      json={"name": "TEST_Living Room", "room_type": "living"})
    assert r.status_code == 200, r.text
    room_id = r.json()["id"]
    yield room_id
    requests.delete(f"{API}/rooms/{room_id}", headers=h)


# ============================================================
# Room CRUD
# ============================================================
class TestRoomCRUD:
    def test_create_room_returns_empty_galleries(self, admin_token, admin_project):
        h = {"Authorization": f"Bearer {admin_token}"}
        r = requests.post(f"{API}/projects/{admin_project}/rooms", headers=h,
                          json={"name": "TEST_Bedroom A", "room_type": "bedroom"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == "TEST_Bedroom A"
        assert data["room_type"] == "bedroom"
        assert data["current_site_photos"] == []
        assert data["moodboards"] == []
        assert data["reference_images"] == []
        assert data["concept_overview"] == ""
        assert data["share_enabled"] is False
        assert data.get("share_slug")
        assert "id" in data
        requests.delete(f"{API}/rooms/{data['id']}", headers=h)

    def test_create_room_bad_type_400(self, admin_token, admin_project):
        h = {"Authorization": f"Bearer {admin_token}"}
        r = requests.post(f"{API}/projects/{admin_project}/rooms", headers=h,
                          json={"name": "TEST_Bad", "room_type": "spaceship"})
        assert r.status_code == 400

    def test_list_rooms_ordered_by_order(self, admin_token, admin_project, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        r = requests.get(f"{API}/projects/{admin_project}/rooms", headers=h)
        assert r.status_code == 200
        rooms = r.json()
        assert isinstance(rooms, list)
        assert any(rm["id"] == created_room for rm in rooms)
        orders = [rm.get("order", 0) for rm in rooms]
        assert orders == sorted(orders)

    def test_get_room_by_id(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        r = requests.get(f"{API}/rooms/{created_room}", headers=h)
        assert r.status_code == 200
        assert r.json()["id"] == created_room

    def test_get_room_invalid_id_400(self, admin_token):
        h = {"Authorization": f"Bearer {admin_token}"}
        r = requests.get(f"{API}/rooms/not-an-oid", headers=h)
        assert r.status_code in (400, 404)

    def test_patch_room_fields(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        r = requests.patch(f"{API}/rooms/{created_room}", headers=h, json={
            "name": "TEST_Living Renamed",
            "room_type": "kitchen",
            "concept_overview": "Designer draft text",
            "designer_notes": "Notes here",
            "pinned_material_row_ids": ["Ceiling", "Wall"],
            "pinned_product_ids": ["p1", "p2"],
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["name"] == "TEST_Living Renamed"
        assert d["room_type"] == "kitchen"
        assert d["concept_overview"] == "Designer draft text"
        assert d["pinned_material_row_ids"] == ["Ceiling", "Wall"]
        assert d["pinned_product_ids"] == ["p1", "p2"]

    def test_patch_bad_room_type_400(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        r = requests.patch(f"{API}/rooms/{created_room}", headers=h, json={"room_type": "nope"})
        assert r.status_code == 400

    def test_delete_room(self, admin_token, admin_project):
        h = {"Authorization": f"Bearer {admin_token}"}
        r = requests.post(f"{API}/projects/{admin_project}/rooms", headers=h,
                          json={"name": "TEST_ToDelete", "room_type": "office"})
        rid = r.json()["id"]
        r = requests.delete(f"{API}/rooms/{rid}", headers=h)
        assert r.status_code == 200
        r = requests.get(f"{API}/rooms/{rid}", headers=h)
        assert r.status_code == 404


# ============================================================
# Ownership isolation
# ============================================================
class TestOwnership:
    def test_designer_cannot_access_admin_room(self, admin_token, designer_token, created_room):
        h = {"Authorization": f"Bearer {designer_token}"}
        r = requests.get(f"{API}/rooms/{created_room}", headers=h)
        assert r.status_code == 404

    def test_designer_cannot_create_room_in_admin_project(self, designer_token, admin_project):
        h = {"Authorization": f"Bearer {designer_token}"}
        r = requests.post(f"{API}/projects/{admin_project}/rooms", headers=h,
                          json={"name": "TEST_Hack", "room_type": "living"})
        assert r.status_code == 404


# ============================================================
# Image upload
# ============================================================
class TestRoomImages:
    def test_upload_image_all_three_kinds(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        for kind in ("current_site", "moodboard", "reference"):
            files = {"file": (f"{kind}.png", TINY_PNG, "image/png")}
            r = requests.post(f"{API}/rooms/{created_room}/images?kind={kind}", headers=h, files=files)
            assert r.status_code == 200, f"{kind}: {r.text}"
            data = r.json()
            assert data["id"]
            assert data["mime"] == "image/png"

    def test_upload_invalid_kind_400(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        files = {"file": ("x.png", TINY_PNG, "image/png")}
        r = requests.post(f"{API}/rooms/{created_room}/images?kind=badkind", headers=h, files=files)
        assert r.status_code == 400

    def test_upload_non_image_mime_400(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        files = {"file": ("x.txt", b"hello", "text/plain")}
        r = requests.post(f"{API}/rooms/{created_room}/images?kind=current_site", headers=h, files=files)
        assert r.status_code == 400

    def test_upload_too_large_400(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (7 * 1024 * 1024)
        files = {"file": ("big.png", big, "image/png")}
        r = requests.post(f"{API}/rooms/{created_room}/images?kind=current_site", headers=h, files=files)
        assert r.status_code == 400

    def test_get_and_delete_image(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        files = {"file": ("y.png", TINY_PNG, "image/png")}
        r = requests.post(f"{API}/rooms/{created_room}/images?kind=moodboard", headers=h, files=files)
        img_id = r.json()["id"]
        # GET data_url
        r = requests.get(f"{API}/rooms/{created_room}/images/moodboard/{img_id}", headers=h)
        assert r.status_code == 200
        assert r.json()["data_url"].startswith("data:image/png;base64,")
        # DELETE
        r = requests.delete(f"{API}/rooms/{created_room}/images/moodboard/{img_id}", headers=h)
        assert r.status_code == 200
        # 404 after delete
        r = requests.delete(f"{API}/rooms/{created_room}/images/moodboard/{img_id}", headers=h)
        assert r.status_code == 404


# ============================================================
# AI overview generation
# ============================================================
class TestOverview:
    def test_generate_overview_does_not_overwrite_designer_concept(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        # Set designer concept_overview first
        r = requests.patch(f"{API}/rooms/{created_room}", headers=h,
                           json={"concept_overview": "DESIGNER_FINAL"})
        assert r.status_code == 200
        # Generate AI draft
        r = requests.post(f"{API}/rooms/{created_room}/generate-overview", headers=h)
        assert r.status_code in (200, 502), r.text
        if r.status_code == 502:
            pytest.skip("LLM upstream failure (502) — flagged")
        draft = r.json().get("draft", "")
        assert isinstance(draft, str)
        wc = len(draft.split())
        assert wc >= 20, f"Draft too short: {wc} words"
        # Room fetch: concept_overview must still be DESIGNER_FINAL
        r = requests.get(f"{API}/rooms/{created_room}", headers=h)
        assert r.json()["concept_overview"] == "DESIGNER_FINAL"


# ============================================================
# Share + public
# ============================================================
class TestSharePublic:
    def test_share_toggle_and_public_get(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        # Pin something then enable share
        requests.patch(f"{API}/rooms/{created_room}", headers=h, json={
            "concept_overview": "Warm modern living room",
            "designer_notes": "Client loves texture",
            "pinned_material_row_ids": ["Ceiling"],
            "pinned_product_ids": ["prod1"],
        })
        r = requests.post(f"{API}/rooms/{created_room}/share", headers=h, json={"enabled": True})
        assert r.status_code == 200
        d = r.json()
        assert d["share_enabled"] is True
        assert d["share_slug"]
        slug = d["share_slug"]
        # Public GET (no auth)
        r = requests.get(f"{API}/public/rooms/{slug}")
        assert r.status_code == 200
        pub = r.json()
        assert pub["name"] and pub["share_slug"] == slug
        assert "pinned_material_rows" in pub
        assert "pinned_products" in pub
        assert pub["concept_overview"] == "Warm modern living room"
        # Security: forbidden fields not leaked
        assert "user_id" not in pub
        assert "password_hash" not in pub
        assert "concept_overview_ai_draft" not in pub
        # Disable
        r = requests.post(f"{API}/rooms/{created_room}/share", headers=h, json={"enabled": False})
        assert r.status_code == 200
        # Public GET now 404
        r = requests.get(f"{API}/public/rooms/{slug}")
        assert r.status_code == 404

    def test_public_image_no_auth(self, admin_token, created_room):
        h = {"Authorization": f"Bearer {admin_token}"}
        # Upload image + enable share
        files = {"file": ("z.png", TINY_PNG, "image/png")}
        r = requests.post(f"{API}/rooms/{created_room}/images?kind=current_site", headers=h, files=files)
        img_id = r.json()["id"]
        r = requests.post(f"{API}/rooms/{created_room}/share", headers=h, json={"enabled": True})
        slug = r.json()["share_slug"]
        # Fetch image WITHOUT auth
        r = requests.get(f"{API}/public/rooms/{slug}/images/current_site/{img_id}")
        assert r.status_code == 200
        assert r.json()["data_url"].startswith("data:image/png;base64,")
