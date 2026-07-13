"""Sprint 8 - MaterialMatch Studio end-to-end backend tests.

Covers:
- Non-admin 403 on all Studio endpoints
- PDF upload → draft records → approve → published in library
- Non-PDF rejected (400)
- Uploaded published records outrank seeded on /admin/knowledge-engine
- Publish-all convenience endpoint
"""
import io
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://design-match-ai.preview.emergentagent.com").rstrip("/")
ADMIN = {"email": "admin@materialmatch.ai", "password": "MaterialAdmin2026!"}
USER = {"email": "designer@test.com", "password": "Designer2026!"}


def _make_pdf(brand: str, mat: str, code: str) -> bytes:
    import fitz
    d = fitz.open()
    p = d.new_page()
    # Embed a raster PNG swatch (the extractor scans embedded images, not
    # vector paths). PNG is a 200x200 solid-colour square.
    try:
        from PIL import Image
        _swatch = Image.new("RGB", (200, 200), (140, 90, 52))
        _buf = io.BytesIO()
        _swatch.save(_buf, format="PNG")
        p.insert_image(fitz.Rect(72, 240, 260, 380), stream=_buf.getvalue())
    except Exception:
        pass
    p.insert_text((72, 90), brand)
    p.insert_text((72, 130), f"{mat} — Product")
    p.insert_text((72, 170), f"Code: {code}")
    p.insert_text((72, 210), "Premium laminate for interior walls")
    buf = io.BytesIO()
    d.save(buf)
    d.close()
    return buf.getvalue()


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=20)
    assert r.status_code == 200, f"admin login failed {r.status_code} {r.text[:200]}"
    return s


@pytest.fixture(scope="module")
def user_session():
    s = requests.Session()
    # Register may 409 if exists; try login regardless
    s.post(f"{BASE_URL}/api/auth/register",
           json={"email": USER["email"], "password": USER["password"], "name": "Test Designer"}, timeout=20)
    r = s.post(f"{BASE_URL}/api/auth/login", json=USER, timeout=20)
    if r.status_code != 200:
        pytest.skip("non-admin user login failed")
    return s


def _wait_for_extraction(session, upload_id: str, timeout: int = 30) -> dict:
    """Poll the uploads list until the given upload reaches a terminal
    status (review / published / failed / archived). Upload is now
    processed in a background task so callers must poll rather than
    reading `records_extracted` from the upload response."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = session.get(f"{BASE_URL}/api/admin/studio/uploads", timeout=15)
        assert r.status_code == 200
        u = next((x for x in r.json()["uploads"] if x["id"] == upload_id), None)
        if u and u.get("status") not in (None, "processing"):
            return u
        time.sleep(0.5)
    raise AssertionError(f"upload {upload_id} did not finish within {timeout}s")


class TestStudioAuth:
    def test_upload_requires_admin(self, user_session):
        pdf = _make_pdf("Guard", "Item", "G-1")
        r = user_session.post(f"{BASE_URL}/api/admin/studio/upload",
                              files={"file": ("x.pdf", pdf, "application/pdf")}, timeout=30)
        assert r.status_code == 403, r.text[:200]

    def test_list_requires_admin(self, user_session):
        r = user_session.get(f"{BASE_URL}/api/admin/studio/uploads", timeout=15)
        assert r.status_code == 403

    def test_library_requires_admin(self, user_session):
        r = user_session.get(f"{BASE_URL}/api/admin/studio/library", timeout=15)
        assert r.status_code == 403


class TestStudioValidation:
    def test_non_pdf_rejected(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/admin/studio/upload",
                               files={"file": ("bad.txt", b"hello", "text/plain")}, timeout=20)
        assert r.status_code == 400
        assert "pdf" in r.text.lower()


class TestStudioPipeline:
    """Upload → records draft → approve → library → search prioritization."""

    unique_brand = f"TESTBrand{uuid.uuid4().hex[:6].upper()}"
    unique_material = f"TESTMaterial{uuid.uuid4().hex[:6]}"
    unique_code = f"TC-{uuid.uuid4().hex[:6].upper()}"

    def test_01_upload_pdf(self, admin_session):
        pdf = _make_pdf(self.__class__.unique_brand, self.__class__.unique_material, self.__class__.unique_code)
        r = admin_session.post(f"{BASE_URL}/api/admin/studio/upload",
                               files={"file": ("studio_test.pdf", pdf, "application/pdf")}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert "upload_id" in data
        # Upload is async — status is 'processing' initially, records
        # accumulate as the background task finishes.
        assert data["status"] == "processing"
        self.__class__.upload_id = data["upload_id"]
        upload = _wait_for_extraction(admin_session, data["upload_id"])
        assert upload["records_extracted"] >= 1
        assert upload["status"] == "review"

    def test_02_upload_listed(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads", timeout=15)
        assert r.status_code == 200
        ids = [u["id"] for u in r.json()["uploads"]]
        assert self.__class__.upload_id in ids

    def test_03_records_draft(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads/{self.__class__.upload_id}/records", timeout=15)
        assert r.status_code == 200
        recs = r.json()["records"]
        assert len(recs) >= 1
        assert all(rec["status"] == "draft" for rec in recs)
        # No _id field leaked
        assert all("_id" not in rec for rec in recs)
        self.__class__.record_ids = [rec["id"] for rec in recs]

    def test_04_approve_records(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/admin/studio/records/approve",
                               json={"record_ids": self.__class__.record_ids}, timeout=20)
        assert r.status_code == 200, r.text[:200]
        assert r.json()["approved"] >= 1
        # Verify persisted
        r2 = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads/{self.__class__.upload_id}/records", timeout=15)
        recs = r2.json()["records"]
        assert all(rec["status"] == "published" for rec in recs)

    def test_05_library_lists_published(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/studio/library", timeout=15)
        assert r.status_code == 200
        recs = r.json()["records"]
        matching = [x for x in recs if x["id"] in self.__class__.record_ids]
        assert len(matching) >= 1
        assert all(x["status"] == "published" for x in matching)

    def test_06_ke_search_prioritizes_uploaded(self, admin_session):
        # Search for our unique brand token — uploaded record should appear first
        r = admin_session.get(f"{BASE_URL}/api/admin/knowledge-engine",
                              params={"q": self.__class__.unique_brand, "limit": 50}, timeout=20)
        assert r.status_code == 200
        recs = r.json()["records"]
        assert len(recs) >= 1, "uploaded record not found in KE search"
        # First record should be from Uploaded PDF
        assert recs[0]["source"] == "Uploaded PDF", f"First result not uploaded: {recs[0]}"

    def test_07_approve_empty_400(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/admin/studio/records/approve",
                               json={"record_ids": []}, timeout=15)
        assert r.status_code == 400


class TestPublishAll:
    def test_publish_all_endpoint(self, admin_session):
        pdf = _make_pdf(f"PubAll{uuid.uuid4().hex[:4]}", "Alpha", "P-1")
        r = admin_session.post(f"{BASE_URL}/api/admin/studio/upload",
                               files={"file": ("pub.pdf", pdf, "application/pdf")}, timeout=60)
        assert r.status_code == 200
        uid = r.json()["upload_id"]
        _wait_for_extraction(admin_session, uid)
        r2 = admin_session.post(f"{BASE_URL}/api/admin/studio/uploads/{uid}/publish", timeout=20)
        assert r2.status_code == 200
        assert r2.json()["approved"] >= 1
        # Verify upload status = published
        r3 = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads", timeout=15)
        upload = next(u for u in r3.json()["uploads"] if u["id"] == uid)
        assert upload["status"] == "published"


class TestStudioReject:
    def test_reject_flow(self, admin_session):
        pdf = _make_pdf(f"Rej{uuid.uuid4().hex[:4]}", "Beta", "R-1")
        r = admin_session.post(f"{BASE_URL}/api/admin/studio/upload",
                               files={"file": ("rej.pdf", pdf, "application/pdf")}, timeout=60)
        assert r.status_code == 200
        uid = r.json()["upload_id"]
        _wait_for_extraction(admin_session, uid)
        r2 = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads/{uid}/records", timeout=15)
        rids = [x["id"] for x in r2.json()["records"]]
        r3 = admin_session.post(f"{BASE_URL}/api/admin/studio/records/reject",
                                json={"record_ids": rids}, timeout=15)
        assert r3.status_code == 200
        assert r3.json()["rejected"] >= 1
        r4 = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads/{uid}/records", timeout=15)
        assert all(x["status"] == "rejected" for x in r4.json()["records"])
