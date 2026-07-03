"""Sprint 5A backend tests: auto-populated pins on room creation,
final_render image kind, and enhanced public share endpoint fields."""
import os
import io
import base64
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://design-match-ai.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASS = "MaterialAdmin2026!"


# -- tiny 1x1 PNG for image upload tests --
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
               timeout=20)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text[:200]}"
    return s


@pytest.fixture(scope="module")
def owned_project_id(admin_session):
    """Return an existing owned project id that has both mock_analysis rows
    and products_detected — required to validate auto-populated pins."""
    r = admin_session.get(f"{API}/projects", timeout=20)
    assert r.status_code == 200
    projects = r.json()
    # pick a project that has rows + products
    for p in projects:
        pid = p.get("id")
        if not pid:
            continue
        d = admin_session.get(f"{API}/projects/{pid}", timeout=20).json()
        rows = ((d.get("mock_analysis") or {}).get("rows")) or []
        prods = ((d.get("products_detected") or {}).get("products")) or []
        if rows and prods:
            return pid
    pytest.skip("No admin-owned project with rows+products found")


@pytest.fixture
def temp_room(admin_session, owned_project_id):
    """Create a fresh room, yield, delete."""
    r = admin_session.post(
        f"{API}/projects/{owned_project_id}/rooms",
        json={"name": "TEST_Sprint5A_Room", "room_type": "living"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    room = r.json()
    yield room
    admin_session.delete(f"{API}/rooms/{room['id']}", timeout=20)


# ============================================================================
# Auto-populated pins on room creation (Sprint 5A)
# ============================================================================
class TestAutoPopulatePins:
    def test_new_room_autopopulates_material_and_product_pins(
        self, admin_session, owned_project_id, temp_room
    ):
        # Verify the create response
        assert "final_render_images" in temp_room, "new room must include final_render_images"
        assert temp_room["final_render_images"] == []

        # Fetch project to know expected pins
        proj = admin_session.get(f"{API}/projects/{owned_project_id}", timeout=20).json()
        expected_rows = ((proj.get("mock_analysis") or {}).get("rows")) or []
        expected_products = ((proj.get("products_detected") or {}).get("products")) or []

        # Fetch fresh room detail
        r = admin_session.get(f"{API}/rooms/{temp_room['id']}", timeout=20)
        assert r.status_code == 200
        room = r.json()
        # Should have SOME pinned material rows and SOME pinned products (up to 16)
        pinned_rows = room.get("pinned_material_row_ids", [])
        pinned_products = room.get("pinned_product_ids", [])
        assert len(pinned_rows) > 0, "pinned_material_row_ids should be autopopulated"
        assert len(pinned_products) > 0, "pinned_product_ids should be autopopulated"
        assert len(pinned_rows) <= 16
        assert len(pinned_products) <= 16
        # Expected: pinned count == min(16, len(expected_*))
        assert len(pinned_rows) == min(16, len([r_ for r_ in expected_rows
                                                if (r_.get("zone") or r_.get("id"))]))
        assert len(pinned_products) == min(16, len([p for p in expected_products if p.get("id")]))


# ============================================================================
# final_render image kind (Sprint 5A)
# ============================================================================
class TestFinalRenderImageKind:
    def test_upload_get_delete_final_render(self, admin_session, temp_room):
        room_id = temp_room["id"]
        # UPLOAD
        files = {"file": ("test.png", io.BytesIO(_PNG_1x1), "image/png")}
        r = admin_session.post(
            f"{API}/rooms/{room_id}/images",
            params={"kind": "final_render"},
            files=files,
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("kind") == "final_render"
        img_id = body["id"]

        # GET
        r = admin_session.get(
            f"{API}/rooms/{room_id}/images/final_render/{img_id}", timeout=20
        )
        assert r.status_code == 200, r.text
        assert r.json()["data_url"].startswith("data:image/png;base64,")

        # DELETE
        r = admin_session.delete(
            f"{API}/rooms/{room_id}/images/final_render/{img_id}", timeout=20
        )
        assert r.status_code == 200, r.text
        # GET again -> 404
        r = admin_session.get(
            f"{API}/rooms/{room_id}/images/final_render/{img_id}", timeout=20
        )
        assert r.status_code == 404

    def test_invalid_kind_rejected(self, admin_session, temp_room):
        files = {"file": ("test.png", io.BytesIO(_PNG_1x1), "image/png")}
        r = admin_session.post(
            f"{API}/rooms/{temp_room['id']}/images",
            params={"kind": "foo"},
            files=files,
            timeout=20,
        )
        assert r.status_code == 400


# ============================================================================
# Enhanced public share endpoint (Sprint 5A)
# ============================================================================
class TestPublicShareDemo:
    def test_public_demo_has_catalogue_matches_and_final_render_and_designer_name(self):
        # Anonymous request — no session cookies
        r = requests.get(f"{API}/public/rooms/materialmatch-demo", timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        # New Sprint 5A fields present
        assert "catalogue_matches" in data
        assert "final_render_images" in data
        assert "designer_name" in data
        assert isinstance(data["catalogue_matches"], list)
        assert isinstance(data["final_render_images"], list)
        # Demo: 3 catalogue matches, 0 final renders, null designer
        assert len(data["catalogue_matches"]) == 3, f"expected 3, got {len(data['catalogue_matches'])}"
        assert data["final_render_images"] == []
        assert data["designer_name"] is None
        # Pinned counts (as per problem statement: 4 rows + 4 products)
        assert len(data.get("pinned_material_rows", [])) == 4
        assert len(data.get("pinned_products", [])) == 4
        # NO sensitive leaks
        raw = r.text
        assert "password_hash" not in raw
        assert "\"user_id\"" not in raw
        assert "email" not in raw.lower() or "materialmatch-demo" in raw  # demo has no owner email


# ============================================================================
# Copy audit (Sprint 5A) — via seed demo project (public, no auth)
# ============================================================================
class TestCopyAudit:
    def test_demo_room_copy_has_no_forbidden_strings(self):
        r = requests.get(f"{API}/public/rooms/materialmatch-demo", timeout=20)
        assert r.status_code == 200
        text = r.text
        # Allowed: Backend can contain concept_overview text, but shouldn't
        # contain these UI labels in the payload strings.
        for forbidden in ["AI Summary", "Generated by AI"]:
            assert forbidden not in text, f"public payload must not contain '{forbidden}'"
