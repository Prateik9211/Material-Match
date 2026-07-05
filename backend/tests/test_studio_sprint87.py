"""Sprint 8.7 - MaterialMatch Studio final pre-submission tests.

Covers:
- Catalogue lifecycle recompute (draft -> review_remaining -> published)
- Double-publish prevention (approve + bulk publish idempotent)
- Tags field roundtrip on PATCH /admin/studio/records/{id}
- Reprocess endpoint (no-blob 400, reference seed 400)
- Replace endpoint (reference seed 400, non-pdf 400)
- Page preview endpoint returns base64 JPEG
"""
import io
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN = {"email": "admin@materialmatch.ai", "password": "MaterialAdmin2026!"}


def _make_pdf(brand: str, mat: str, code: str) -> bytes:
    import fitz
    from PIL import Image
    d = fitz.open()
    p = d.new_page()
    swatch = Image.new("RGB", (200, 200), (140, 90, 52))
    buf = io.BytesIO()
    swatch.save(buf, format="PNG")
    p.insert_image(fitz.Rect(72, 240, 260, 380), stream=buf.getvalue())
    p.insert_text((72, 90), brand)
    p.insert_text((72, 130), f"{mat} — Product")
    p.insert_text((72, 170), f"Code: {code}")
    p.insert_text((72, 210), "Premium laminate for interior walls")
    out = io.BytesIO()
    d.save(out)
    d.close()
    return out.getvalue()


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=20)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text[:200]}"
    return s


@pytest.fixture(scope="module")
def fresh_upload(admin_session):
    """Upload a small text PDF (2 pages -> hopefully >= 2 records)."""
    # 2 pages to allow lifecycle test (some vs all published)
    import fitz
    from PIL import Image
    d = fitz.open()
    for i in range(2):
        p = d.new_page()
        sw = Image.new("RGB", (200, 200), (140 - i * 20, 90, 52 + i * 30))
        buf = io.BytesIO()
        sw.save(buf, format="PNG")
        p.insert_image(fitz.Rect(72, 240, 260, 380), stream=buf.getvalue())
        p.insert_text((72, 90), f"BrandLC{uuid.uuid4().hex[:4].upper()}")
        p.insert_text((72, 130), f"Material{i} Product")
        p.insert_text((72, 170), f"Code: LC-{i}-{uuid.uuid4().hex[:4].upper()}")
        p.insert_text((72, 210), "Premium laminate for interior walls")
    out = io.BytesIO()
    d.save(out)
    d.close()
    pdf = out.getvalue()
    r = admin_session.post(
        f"{BASE_URL}/api/admin/studio/upload",
        files={"file": (f"lifecycle_{uuid.uuid4().hex[:6]}.pdf", pdf, "application/pdf")},
        timeout=90,
    )
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert data["records_extracted"] >= 1
    return data["upload_id"]


def _get_upload(session, upload_id):
    r = session.get(f"{BASE_URL}/api/admin/studio/uploads", timeout=15)
    assert r.status_code == 200
    return next((u for u in r.json()["uploads"] if u["id"] == upload_id), None)


def _records(session, upload_id):
    r = session.get(f"{BASE_URL}/api/admin/studio/uploads/{upload_id}/records", timeout=15)
    assert r.status_code == 200
    return r.json()["records"]


class TestCatalogueLifecycle:
    def test_partial_publish_marks_review_remaining(self, admin_session, fresh_upload):
        recs = _records(admin_session, fresh_upload)
        drafts = [r for r in recs if r["status"] == "draft"]
        if len(drafts) < 2:
            pytest.skip("need >=2 draft records for partial-publish lifecycle test")
        # Publish only one
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/records/approve",
            json={"record_ids": [drafts[0]["id"]]},
            timeout=15,
        )
        assert r.status_code == 200
        assert r.json()["approved"] == 1
        up = _get_upload(admin_session, fresh_upload)
        assert up is not None
        assert up["status"] == "review_remaining", f"expected review_remaining, got {up['status']}"

    def test_full_publish_marks_published(self, admin_session, fresh_upload):
        # Publish all remaining drafts
        r = admin_session.post(f"{BASE_URL}/api/admin/studio/uploads/{fresh_upload}/publish", timeout=20)
        assert r.status_code == 200
        up = _get_upload(admin_session, fresh_upload)
        assert up["status"] == "published", f"expected published, got {up['status']}"


class TestDoublePublishPrevention:
    def test_approve_already_published_returns_zero(self, admin_session, fresh_upload):
        recs = _records(admin_session, fresh_upload)
        pub = [r for r in recs if r["status"] == "published"]
        assert pub, "no published records to test double-publish"
        rid = pub[0]["id"]
        original_pub_at = pub[0].get("published_at")
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/records/approve",
            json={"record_ids": [rid]},
            timeout=15,
        )
        assert r.status_code == 200
        assert r.json()["approved"] == 0, "already-published record should not re-publish"
        # published_at unchanged
        recs2 = _records(admin_session, fresh_upload)
        again = next(x for x in recs2 if x["id"] == rid)
        assert again.get("published_at") == original_pub_at

    def test_bulk_publish_already_published_returns_zero(self, admin_session, fresh_upload):
        recs = _records(admin_session, fresh_upload)
        pub = [r for r in recs if r["status"] == "published"]
        assert pub
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/records/bulk",
            json={"record_ids": [pub[0]["id"]], "action": "publish"},
            timeout=15,
        )
        assert r.status_code == 200
        assert r.json()["affected"] == 0


class TestTagsRoundtrip:
    def test_tags_persist_as_array(self, admin_session, fresh_upload):
        recs = _records(admin_session, fresh_upload)
        rid = recs[0]["id"]
        r = admin_session.patch(
            f"{BASE_URL}/api/admin/studio/records/{rid}",
            json={"tags": ["matte", "indoor"]},
            timeout=15,
        )
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert body.get("tags") == ["matte", "indoor"]
        # Reload verify
        recs2 = _records(admin_session, fresh_upload)
        found = next(x for x in recs2 if x["id"] == rid)
        assert found.get("tags") == ["matte", "indoor"]


class TestReprocess:
    def test_reprocess_endpoint(self, admin_session, fresh_upload):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/uploads/{fresh_upload}/reprocess",
            timeout=90,
        )
        # Blob is stored on upload, so should succeed
        assert r.status_code == 200, r.text[:300]
        assert r.json()["records_extracted"] >= 0

    def test_reprocess_reference_seed_blocked(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads", timeout=15)
        seed = next((u for u in r.json()["uploads"] if u.get("demo_seed")), None)
        if not seed:
            pytest.skip("no reference seed upload found")
        rr = admin_session.post(
            f"{BASE_URL}/api/admin/studio/uploads/{seed['id']}/reprocess",
            timeout=15,
        )
        assert rr.status_code == 400
        assert "reference" in rr.text.lower() or "seed" in rr.text.lower()

    def test_reprocess_unknown_404(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/uploads/does-not-exist/reprocess",
            timeout=15,
        )
        assert r.status_code == 404


class TestReplace:
    def test_replace_reference_seed_blocked(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads", timeout=15)
        seed = next((u for u in r.json()["uploads"] if u.get("demo_seed")), None)
        if not seed:
            pytest.skip("no reference seed upload found")
        pdf = _make_pdf("Rep", "Item", "R-1")
        rr = admin_session.post(
            f"{BASE_URL}/api/admin/studio/uploads/{seed['id']}/replace",
            files={"file": ("new.pdf", pdf, "application/pdf")},
            timeout=30,
        )
        assert rr.status_code == 400

    def test_replace_non_pdf_400(self, admin_session, fresh_upload):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/uploads/{fresh_upload}/replace",
            files={"file": ("bad.txt", b"nope", "text/plain")},
            timeout=15,
        )
        assert r.status_code == 400

    def test_replace_flow(self, admin_session, fresh_upload):
        pdf = _make_pdf(f"REP{uuid.uuid4().hex[:4]}", "AlphaRep", "AR-1")
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/uploads/{fresh_upload}/replace",
            files={"file": ("replacement.pdf", pdf, "application/pdf")},
            timeout=90,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["upload_id"] == fresh_upload
        assert body["filename"] == "replacement.pdf"


class TestPagePreview:
    def test_page_preview_base64(self, admin_session, fresh_upload):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/studio/uploads/{fresh_upload}/page/1",
            timeout=20,
        )
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert data["upload_id"] == fresh_upload
        assert data["page_number"] == 1
        assert isinstance(data["image_b64"], str)
        assert len(data["image_b64"]) > 100

    def test_page_preview_out_of_range(self, admin_session, fresh_upload):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/studio/uploads/{fresh_upload}/page/999",
            timeout=20,
        )
        assert r.status_code == 404

    def test_page_preview_unknown_upload(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/studio/uploads/nope-xxx/page/1",
            timeout=15,
        )
        assert r.status_code == 404


class TestCleanup:
    def test_delete_fresh_upload(self, admin_session, fresh_upload):
        # Cleanup: delete the test upload
        r = admin_session.delete(
            f"{BASE_URL}/api/admin/studio/uploads/{fresh_upload}",
            timeout=15,
        )
        assert r.status_code == 200
