"""
MaterialMatch AI - Mock material analysis flow tests (MVP, no LLM).
Covers:
- POST /api/projects/{id}/mock-analyze happy path
- 400 when no reference image uploaded
- 401 when unauthenticated
- 404 for invalid/foreign project
- Stable/idempotent results across calls (deterministic per project_id)
- Side effects: project.status='completed', project.mock_analysis persisted
- GET /api/projects/{id} returns mock_analysis after analysis
"""
import os
import io
import uuid
import pytest
import requests
from PIL import Image, ImageDraw

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break
API = f"{BASE_URL}/api"


def _make_jpeg(seed=0, w=400, h=300):
    img = Image.new("RGB", (w, h), (220, 200, 170))
    d = ImageDraw.Draw(img)
    for y in range(h):
        c = int(180 + 60 * (y / h))
        d.line([(0, y), (w, y)], fill=(c, c - 20, c - 50))
    for i in range(0, w, 40):
        d.rectangle([i, 50, i + 35, h - 50], outline=(90, 60, 30), width=2)
    d.ellipse([w // 2 - 40 + seed * 10, 30, w // 2 + 40 + seed * 10, 110],
              fill=(255, 240, 200), outline=(180, 150, 100), width=3)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


# Module-scoped fixtures (auth + a project with reference)
@pytest.fixture(scope="module")
def authed_session():
    s = requests.Session()
    email = f"TEST_mock_{uuid.uuid4().hex[:8]}@test.com"
    pwd = "MockTest2026!"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": pwd, "name": "TEST_Mock"})
    assert r.status_code == 200, r.text
    s._email = email  # noqa
    return s


@pytest.fixture(scope="module")
def project_with_ref(authed_session):
    s = authed_session
    r = s.post(f"{API}/projects",
               json={"name": "TEST_MockAnalyze", "client_name": "TEST_Client"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    # upload reference
    files = {"file": ("ref.jpg", _make_jpeg(seed=1), "image/jpeg")}
    ru = s.post(f"{API}/projects/{pid}/reference", files=files)
    assert ru.status_code == 200, ru.text
    yield pid
    # cleanup
    s.delete(f"{API}/projects/{pid}")


@pytest.fixture(scope="module")
def project_without_ref(authed_session):
    s = authed_session
    r = s.post(f"{API}/projects", json={"name": "TEST_MockAnalyze_NoRef"})
    assert r.status_code == 200
    pid = r.json()["id"]
    yield pid
    s.delete(f"{API}/projects/{pid}")


# -------- Auth guard --------
def test_mock_analyze_unauthenticated(project_with_ref):
    r = requests.post(f"{API}/projects/{project_with_ref}/mock-analyze")
    assert r.status_code == 401


# -------- 400 when ref missing --------
def test_mock_analyze_requires_reference(authed_session, project_without_ref):
    r = authed_session.post(f"{API}/projects/{project_without_ref}/mock-analyze")
    assert r.status_code == 400
    detail = (r.json().get("detail") or "").lower()
    assert "reference" in detail


# -------- Happy path: structure + side effects --------
def test_mock_analyze_happy_path(authed_session, project_with_ref):
    r = authed_session.post(f"{API}/projects/{project_with_ref}/mock-analyze")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "rows" in data and isinstance(data["rows"], list)
    assert len(data["rows"]) >= 5
    assert "generated_at" in data
    assert data.get("version") == "mock-v1"

    required_keys = {"zone", "material_type", "color", "texture",
                     "finish", "design_style", "keywords", "confidence"}
    for row in data["rows"]:
        assert required_keys.issubset(set(row.keys())), f"missing keys in row: {row}"
        assert isinstance(row["keywords"], list)
        assert 0.0 <= float(row["confidence"]) <= 1.0


def test_mock_analyze_persists_to_project(authed_session, project_with_ref):
    # GET project should now have mock_analysis + status completed
    r = authed_session.get(f"{API}/projects/{project_with_ref}")
    assert r.status_code == 200
    p = r.json()
    assert p["status"] == "completed"
    assert "mock_analysis" in p
    ma = p["mock_analysis"]
    assert isinstance(ma.get("rows"), list) and len(ma["rows"]) >= 5
    assert ma.get("version") == "mock-v1"


# -------- Idempotency / stability --------
def test_mock_analyze_is_stable(authed_session, project_with_ref):
    r1 = authed_session.post(f"{API}/projects/{project_with_ref}/mock-analyze")
    r2 = authed_session.post(f"{API}/projects/{project_with_ref}/mock-analyze")
    assert r1.status_code == 200 and r2.status_code == 200
    rows1 = r1.json()["rows"]
    rows2 = r2.json()["rows"]
    assert len(rows1) == len(rows2)
    # Compare relevant fields (ignoring generated_at)
    for a, b in zip(rows1, rows2):
        assert a["zone"] == b["zone"]
        assert a["material_type"] == b["material_type"]
        assert a["confidence"] == b["confidence"]


# -------- Cross-user/404 isolation --------
def test_mock_analyze_other_user_404(project_with_ref):
    s2 = requests.Session()
    email = f"TEST_other_{uuid.uuid4().hex[:6]}@t.com"
    s2.post(f"{API}/auth/register",
            json={"email": email, "password": "OtherPass2026!", "name": "Other"})
    r = s2.post(f"{API}/projects/{project_with_ref}/mock-analyze")
    assert r.status_code == 404
