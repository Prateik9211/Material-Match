"""
MaterialMatch AI - Mock Catalogue Match flow tests (MVP, no LLM).
Covers:
- POST /api/projects/{id}/match happy path (5 deterministic matches)
- 400 when zone is missing from analysis
- 401 when unauthenticated
- 404 for foreign project (cross-user isolation)
- Deterministic / idempotent results (same project + zone -> same 5)
- Field shape: id, product_name, catalogue_ref, match_percent in [50,98],
  score_label in {Strong/Good/Partial/Low Match}, reasons (3), disqualifier (str|None)
- Disqualifier present on matches[3] and matches[4]; None on first 3
- Persistence under project.match_results.<zone>
- Optional file uploads stored as metadata only (no bytes)
"""
import os
import io
import uuid
import pytest
import requests
from PIL import Image

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break
API = f"{BASE_URL}/api"

VALID_LABELS = {"Strong Match", "Good Match", "Partial Match", "Low Match"}


def _make_jpeg():
    img = Image.new("RGB", (320, 240), (210, 190, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


# ---- Shared fixtures: authed user + project with mock_analysis already run ----
@pytest.fixture(scope="module")
def authed_session():
    s = requests.Session()
    email = f"TEST_match_{uuid.uuid4().hex[:8]}@test.com"
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "MatchTest2026!", "name": "TEST_Match"})
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="module")
def analysed_project(authed_session):
    s = authed_session
    r = s.post(f"{API}/projects", json={"name": "TEST_Match_Project"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    files = {"file": ("ref.jpg", _make_jpeg(), "image/jpeg")}
    ru = s.post(f"{API}/projects/{pid}/reference", files=files)
    assert ru.status_code == 200, ru.text
    ra = s.post(f"{API}/projects/{pid}/mock-analyze")
    assert ra.status_code == 200, ra.text
    yield pid, ra.json()["rows"]
    s.delete(f"{API}/projects/{pid}")


# -------- Auth guard --------
def test_match_unauthenticated(analysed_project):
    pid, rows = analysed_project
    r = requests.post(f"{API}/projects/{pid}/match", data={"zone": rows[0]["zone"]})
    assert r.status_code == 401


# -------- 400 when zone missing from analysis --------
def test_match_invalid_zone_returns_400(authed_session, analysed_project):
    pid, _ = analysed_project
    r = authed_session.post(f"{API}/projects/{pid}/match",
                            data={"zone": "Definitely Not A Real Zone XYZ"})
    assert r.status_code == 400
    detail = (r.json().get("detail") or "").lower()
    assert "zone" in detail and "not found" in detail


# -------- Happy path: 5 matches w/ required fields --------
def test_match_happy_path_structure(authed_session, analysed_project):
    pid, rows = analysed_project
    zone = rows[0]["zone"]
    r = authed_session.post(f"{API}/projects/{pid}/match", data={"zone": zone})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["zone"] == zone
    assert data.get("version") == "mock-match-v1"
    matches = data["matches"]
    assert isinstance(matches, list) and len(matches) == 5

    required = {"id", "product_name", "catalogue_ref", "match_percent",
                "score_label", "reasons", "disqualifier", "thumbnail_color"}
    for i, m in enumerate(matches):
        assert required.issubset(set(m.keys())), f"row {i} missing keys: {m.keys()}"
        assert isinstance(m["match_percent"], int)
        assert 50 <= m["match_percent"] <= 98
        assert m["score_label"] in VALID_LABELS
        assert isinstance(m["reasons"], list) and len(m["reasons"]) == 3
        assert all(isinstance(r, str) and r for r in m["reasons"])
        assert isinstance(m["thumbnail_color"], str) and m["thumbnail_color"].startswith("#")
        if i < 3:
            assert m["disqualifier"] is None, f"match {i} should have no disqualifier"
        else:
            assert isinstance(m["disqualifier"], str) and len(m["disqualifier"]) > 0


# -------- Deterministic: same project + same zone -> same matches --------
def test_match_deterministic(authed_session, analysed_project):
    pid, rows = analysed_project
    zone = rows[0]["zone"]
    r1 = authed_session.post(f"{API}/projects/{pid}/match", data={"zone": zone})
    r2 = authed_session.post(f"{API}/projects/{pid}/match", data={"zone": zone})
    assert r1.status_code == 200 and r2.status_code == 200
    m1 = r1.json()["matches"]
    m2 = r2.json()["matches"]
    for a, b in zip(m1, m2):
        assert a["product_name"] == b["product_name"]
        assert a["catalogue_ref"] == b["catalogue_ref"]
        assert a["match_percent"] == b["match_percent"]
        assert a["score_label"] == b["score_label"]
        assert a["reasons"] == b["reasons"]
        assert a["disqualifier"] == b["disqualifier"]


# -------- Persistence into project.match_results.<zone> --------
def test_match_persisted_on_project(authed_session, analysed_project):
    pid, rows = analysed_project
    zone = rows[0]["zone"]
    rm = authed_session.post(f"{API}/projects/{pid}/match", data={"zone": zone})
    assert rm.status_code == 200

    rp = authed_session.get(f"{API}/projects/{pid}")
    assert rp.status_code == 200
    proj = rp.json()
    assert "match_results" in proj
    assert zone in proj["match_results"]
    saved = proj["match_results"][zone]
    assert saved["zone"] == zone
    assert len(saved["matches"]) == 5
    assert saved.get("version") == "mock-match-v1"


# -------- File uploads stored as metadata only (no bytes) --------
def test_match_accepts_uploads_metadata_only(authed_session, analysed_project):
    pid, rows = analysed_project
    zone = rows[1]["zone"] if len(rows) > 1 else rows[0]["zone"]
    files = [
        ("catalogue", ("brochure.pdf", b"%PDF-1.4 fake pdf body bytes", "application/pdf")),
        ("catalogue", ("product.jpg", _make_jpeg(), "image/jpeg")),
    ]
    r = authed_session.post(
        f"{API}/projects/{pid}/match",
        data={"zone": zone, "manual_prompt": "Prefer matte finishes"},
        files=files,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["manual_prompt"] == "Prefer matte finishes"
    uf = data["uploaded_files"]
    assert isinstance(uf, list) and len(uf) == 2
    names = {f["name"] for f in uf}
    assert {"brochure.pdf", "product.jpg"} == names
    for f in uf:
        assert set(f.keys()) == {"name", "type", "size"}, f"unexpected keys: {f.keys()}"
        assert f["size"] > 0
        # Verify no byte payload leaked into stored metadata
        assert "content" not in f and "bytes" not in f and "data" not in f


# -------- Cross-user 404 isolation --------
def test_match_other_user_404(analysed_project):
    pid, rows = analysed_project
    s2 = requests.Session()
    email = f"TEST_othmatch_{uuid.uuid4().hex[:6]}@t.com"
    s2.post(f"{API}/auth/register",
            json={"email": email, "password": "OtherMatch2026!", "name": "Other"})
    r = s2.post(f"{API}/projects/{pid}/match", data={"zone": rows[0]["zone"]})
    assert r.status_code == 404
