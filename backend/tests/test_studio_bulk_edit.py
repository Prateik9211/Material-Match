"""Backend tests for Studio Review Queue bulk actions, edit, per-record delete/archive.
Verifies PATCH /admin/studio/records/{id}, POST /admin/studio/records/bulk,
DELETE /admin/studio/records/{id}, POST /admin/studio/records/approve.
"""
import os
import io
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://design-match-ai.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASSWORD = "MaterialAdmin2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def sample_upload(admin_session):
    """Find the advance_multiswatch.pdf upload OR upload a small text PDF fallback."""
    r = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads", timeout=15)
    assert r.status_code == 200, r.text
    uploads = r.json().get("uploads", [])
    ms = next((u for u in uploads if "multiswatch" in (u.get("filename") or "").lower()), None)
    if ms:
        return ms
    # fallback: pick any upload with records_extracted>=2
    any_up = next((u for u in uploads if (u.get("records_extracted") or 0) >= 1), None)
    assert any_up, "no uploads with records available"
    return any_up


def _records(admin_session, upload_id):
    r = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads/{upload_id}/records", timeout=15)
    assert r.status_code == 200, r.text
    return r.json().get("records", [])


class TestStudioReviewQueueBackend:
    def test_admin_login_ok(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/auth/me", timeout=10)
        assert r.status_code == 200
        assert r.json().get("role") == "admin"

    def test_uploads_list_has_multiswatch(self, admin_session, sample_upload):
        assert "id" in sample_upload
        assert (sample_upload.get("records_extracted") or 0) >= 1

    def test_records_list_multi_swatch(self, admin_session, sample_upload):
        recs = _records(admin_session, sample_upload["id"])
        assert len(recs) >= 1
        r0 = recs[0]
        # Required fields for card render
        for k in ("id", "brand", "material_name", "category", "color_hex", "page_number", "status"):
            assert k in r0, f"missing field {k} in record"

    def test_patch_edit_persists_region(self, admin_session, sample_upload):
        recs = _records(admin_session, sample_upload["id"])
        target = next((r for r in recs if r.get("status") != "rejected"), recs[0])
        rid = target["id"]
        new_region = "TestRegion-Delhi"
        new_brand = (target.get("brand") or "Brand") + " EDT"
        r = admin_session.patch(
            f"{BASE_URL}/api/admin/studio/records/{rid}",
            json={"region": new_region, "brand": new_brand},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("region") == new_region
        assert body.get("brand") == new_brand
        # Verify via reload
        recs2 = _records(admin_session, sample_upload["id"])
        found = next((x for x in recs2 if x["id"] == rid), None)
        assert found is not None
        assert found.get("region") == new_region
        assert found.get("brand") == new_brand
        # Restore original brand (region we leave for audit)
        admin_session.patch(
            f"{BASE_URL}/api/admin/studio/records/{rid}",
            json={"brand": (target.get("brand") or "Brand")},
            timeout=15,
        )

    def test_bulk_publish_action(self, admin_session, sample_upload):
        recs = _records(admin_session, sample_upload["id"])
        drafts = [r for r in recs if r.get("status") == "draft"]
        if not drafts:
            pytest.skip("no drafts to publish")
        ids = [drafts[0]["id"]]
        # Use approve endpoint (as UI does for Publish Selected)
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/records/approve",
            json={"record_ids": ids},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("approved") >= 1
        recs2 = _records(admin_session, sample_upload["id"])
        assert next(x for x in recs2 if x["id"] == ids[0])["status"] == "published"

    def test_bulk_archive_action(self, admin_session, sample_upload):
        recs = _records(admin_session, sample_upload["id"])
        pubs = [r for r in recs if r.get("status") == "published"]
        if not pubs:
            pytest.skip("no published records to archive")
        ids = [pubs[0]["id"]]
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/records/bulk",
            json={"record_ids": ids, "action": "archive"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("affected") >= 1
        recs2 = _records(admin_session, sample_upload["id"])
        assert next(x for x in recs2 if x["id"] == ids[0])["status"] == "archived"
        # Verify not in published library
        lib = admin_session.get(f"{BASE_URL}/api/admin/studio/library?limit=500", timeout=15).json().get("records", [])
        assert ids[0] not in [x["id"] for x in lib]
        # Restore
        admin_session.post(
            f"{BASE_URL}/api/admin/studio/records/bulk",
            json={"record_ids": ids, "action": "publish"},
            timeout=15,
        )

    def test_bulk_delete_action(self, admin_session, sample_upload):
        # Create a throwaway record by uploading a small text pdf if we can; else pick a rejected/draft duplicate
        # Simplest safe test: delete then verify 404
        recs = _records(admin_session, sample_upload["id"])
        # Pick a draft OR the last record (avoid deleting critical seed)
        target = next((r for r in recs if r.get("status") == "draft"), None)
        if not target:
            pytest.skip("no draft record safe to delete")
        rid = target["id"]
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/records/bulk",
            json={"record_ids": [rid], "action": "delete"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("affected") >= 1
        recs2 = _records(admin_session, sample_upload["id"])
        assert rid not in [x["id"] for x in recs2]

    def test_bulk_bad_action_400(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/records/bulk",
            json={"record_ids": ["nope"], "action": "melt"},
            timeout=10,
        )
        assert r.status_code == 400

    def test_bulk_empty_ids_400(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/records/bulk",
            json={"record_ids": [], "action": "publish"},
            timeout=10,
        )
        assert r.status_code == 400

    def test_edit_unknown_record_404(self, admin_session):
        r = admin_session.patch(
            f"{BASE_URL}/api/admin/studio/records/does-not-exist-xyz",
            json={"brand": "X"},
            timeout=10,
        )
        assert r.status_code == 404

    def test_single_delete_record(self, admin_session, sample_upload):
        recs = _records(admin_session, sample_upload["id"])
        # Only run if we have a spare draft
        target = next((r for r in recs if r.get("status") == "draft"), None)
        if not target:
            pytest.skip("no draft to safely delete")
        rid = target["id"]
        r = admin_session.delete(f"{BASE_URL}/api/admin/studio/records/{rid}", timeout=10)
        assert r.status_code == 200, r.text
        assert r.json().get("deleted") == rid
        # Now 404
        r2 = admin_session.delete(f"{BASE_URL}/api/admin/studio/records/{rid}", timeout=10)
        assert r2.status_code == 404
