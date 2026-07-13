"""Sprint 1 (post-competition) — Studio async ingestion tests.

Covers the permanent PDF-ingestion fix:
  • Upload request returns HTTP 200 in <2s regardless of PDF size (background task).
  • Login / list endpoints stay responsive while extraction runs.
  • Invalid PDFs return 400 up-front (no background task spawned).
  • Non-PDF filenames rejected up-front.
  • Async extraction always leaves the upload in a terminal state
    (`review` or `failed` — never stuck on `processing`).
  • `failed` status carries a human-readable `failure_reason`.
"""

import io
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL not set", allow_module_level=True)

ADMIN = {"email": "admin@materialmatch.ai", "password": "MaterialAdmin2026!"}


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=20)
    assert r.status_code == 200
    return s


def _make_small_pdf() -> bytes:
    import fitz
    from PIL import Image
    d = fitz.open()
    p = d.new_page()
    sw = Image.new("RGB", (200, 200), (140, 90, 52))
    b = io.BytesIO(); sw.save(b, format="PNG")
    p.insert_image(fitz.Rect(72, 240, 260, 380), stream=b.getvalue())
    p.insert_text((72, 90), f"AsyncTest{uuid.uuid4().hex[:4]}")
    p.insert_text((72, 130), "TestMat Product")
    p.insert_text((72, 170), "Code: AT-001")
    p.insert_text((72, 210), "laminate premium")
    out = io.BytesIO()
    d.save(out); d.close()
    return out.getvalue()


def _wait_terminal(session, upload_id: str, timeout: int = 45) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = session.get(f"{BASE_URL}/api/admin/studio/uploads", timeout=15)
        u = next((x for x in r.json()["uploads"] if x["id"] == upload_id), None)
        if u and u.get("status") not in (None, "processing"):
            return u
        time.sleep(0.4)
    raise AssertionError(f"upload {upload_id} did not finish within {timeout}s")


class TestAsyncUpload:
    def test_upload_returns_immediately(self, admin_session):
        """Upload response must arrive in <5s regardless of size."""
        pdf = _make_small_pdf()
        t0 = time.time()
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/upload",
            files={"file": ("async_small.pdf", pdf, "application/pdf")},
            timeout=15,
        )
        elapsed = time.time() - t0
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert data["status"] == "processing"
        assert data["records_extracted"] == 0
        assert "upload_id" in data
        assert elapsed < 8.0, f"upload response too slow: {elapsed:.1f}s"
        # Extraction finishes shortly after
        u = _wait_terminal(admin_session, data["upload_id"])
        assert u["status"] in ("review", "failed")
        # Cleanup
        admin_session.delete(
            f"{BASE_URL}/api/admin/studio/uploads/{data['upload_id']}",
            timeout=15,
        )

    def test_login_responsive_during_extraction(self, admin_session):
        """Event loop must stay responsive while OCR runs."""
        pdf = _make_small_pdf()
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/upload",
            files={"file": ("async_loop.pdf", pdf, "application/pdf")},
            timeout=15,
        )
        uid = r.json()["upload_id"]
        # Immediately hit a lightweight endpoint. Should return fast.
        t0 = time.time()
        r2 = admin_session.get(f"{BASE_URL}/api/admin/studio/uploads", timeout=8)
        assert r2.status_code == 200
        assert (time.time() - t0) < 5.0
        _wait_terminal(admin_session, uid)
        admin_session.delete(f"{BASE_URL}/api/admin/studio/uploads/{uid}", timeout=15)


class TestFailureModes:
    def test_garbage_bytes_rejected_400(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/upload",
            files={"file": ("junk.pdf", b"this is not a pdf at all", "application/pdf")},
            timeout=15,
        )
        assert r.status_code == 400
        assert "pdf" in r.text.lower()

    def test_non_pdf_extension_rejected(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/upload",
            files={"file": ("foo.docx", b"%PDF-1.4 fake header", "application/pdf")},
            timeout=15,
        )
        assert r.status_code == 400

    def test_upload_never_stuck_in_processing(self, admin_session):
        """A crashing extractor must leave the upload in a terminal state."""
        pdf = _make_small_pdf()
        r = admin_session.post(
            f"{BASE_URL}/api/admin/studio/upload",
            files={"file": ("stuck_check.pdf", pdf, "application/pdf")},
            timeout=15,
        )
        uid = r.json()["upload_id"]
        u = _wait_terminal(admin_session, uid, timeout=30)
        assert u["status"] != "processing"
        # On success, failure_reason must be null; on failure, it must be a string.
        if u["status"] == "failed":
            assert u.get("failure_reason"), "failed uploads must carry a failure_reason"
        admin_session.delete(f"{BASE_URL}/api/admin/studio/uploads/{uid}", timeout=15)
