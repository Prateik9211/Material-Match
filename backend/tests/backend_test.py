"""
MaterialMatch AI - Backend API tests.
Covers: auth (register/login/me/logout), projects (CRUD), reference upload,
catalogue upload, analyze launch + polling, reports listing, auth gating.
"""
import os
import io
import time
import base64
import uuid
import pytest
import requests
from PIL import Image, ImageDraw

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fallback to read frontend env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break

API = f"{BASE_URL}/api"

UNIQUE = uuid.uuid4().hex[:8]
TEST_EMAIL = f"test_user_{UNIQUE}@test.com"
TEST_PASSWORD = "TestPass2026!"
TEST_NAME = "TEST_User"

# Shared state across tests
state = {}


def _make_jpeg_with_features(width=400, height=300, seed=0):
    """Create JPEG with real features (gradient + shapes + text-like noise)."""
    img = Image.new("RGB", (width, height), (220, 200, 170))
    draw = ImageDraw.Draw(img)
    # gradient background
    for y in range(height):
        c = int(180 + 60 * (y / height))
        draw.line([(0, y), (width, y)], fill=(c, c - 20, c - 50))
    # add shapes (wood-plank like)
    for i in range(0, width, 40):
        draw.rectangle([i, 50, i + 35, height - 50], outline=(90, 60, 30), width=2)
        draw.line([i + 10, 60, i + 10, height - 60], fill=(120, 80, 40), width=1)
    # add a circle (lamp)
    draw.ellipse([width // 2 - 40 + seed * 10, 30, width // 2 + 40 + seed * 10, 110],
                 fill=(255, 240, 200), outline=(180, 150, 100), width=3)
    # texture noise
    for i in range(0, width, 7):
        draw.point((i, (i * 3) % height), fill=(50, 30, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    return s


# ---------- HEALTH ----------
def test_health_root(session):
    r = session.get(f"{API}/")
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok"


# ---------- AUTH ----------
def test_register(session):
    r = session.post(f"{API}/auth/register",
                     json={"email": TEST_EMAIL, "password": TEST_PASSWORD, "name": TEST_NAME})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["email"] == TEST_EMAIL
    assert data["name"] == TEST_NAME
    assert "id" in data
    state["user_id"] = data["id"]
    # cookie set on session
    assert "access_token" in session.cookies


def test_register_duplicate_email(session):
    r = requests.post(f"{API}/auth/register",
                      json={"email": TEST_EMAIL, "password": TEST_PASSWORD, "name": TEST_NAME})
    assert r.status_code == 400


def test_me_authenticated(session):
    r = session.get(f"{API}/auth/me")
    assert r.status_code == 200
    data = r.json()
    assert data["email"] == TEST_EMAIL
    assert "password_hash" not in data


def test_me_unauthenticated():
    r = requests.get(f"{API}/auth/me")
    assert r.status_code == 401


def test_login_invalid_password():
    r = requests.post(f"{API}/auth/login",
                      json={"email": TEST_EMAIL, "password": "wrong-pass"})
    assert r.status_code == 401


def test_login_success_admin():
    r = requests.post(f"{API}/auth/login",
                      json={"email": "admin@materialmatch.ai",
                            "password": "MaterialAdmin2026!"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["email"] == "admin@materialmatch.ai"
    assert data.get("role") == "admin"


def test_login_existing_user(session):
    # use new requests session to verify login path
    s2 = requests.Session()
    r = s2.post(f"{API}/auth/login",
                json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert r.status_code == 200
    assert "access_token" in s2.cookies


# ---------- PROJECTS ----------
def test_create_project(session):
    r = session.post(f"{API}/projects",
                     json={"name": "TEST_Project_E2E", "client_name": "TEST_Client", "notes": "n/a"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "TEST_Project_E2E"
    assert "id" in data
    assert data["status"] == "draft"
    state["project_id"] = data["id"]


def test_list_projects(session):
    r = session.get(f"{API}/projects")
    assert r.status_code == 200
    data = r.json()
    assert any(p["id"] == state["project_id"] for p in data)
    # ensure heavy fields stripped
    for p in data:
        assert "reference_image_b64" not in p
        assert "catalogue_items" not in p


def test_get_project(session):
    r = session.get(f"{API}/projects/{state['project_id']}")
    assert r.status_code == 200
    assert r.json()["id"] == state["project_id"]


def test_get_project_not_owned():
    # Create another user, try fetch project_id
    s2 = requests.Session()
    other_email = f"other_{uuid.uuid4().hex[:6]}@t.com"
    s2.post(f"{API}/auth/register",
            json={"email": other_email, "password": "OtherPass1!", "name": "Other"})
    r = s2.get(f"{API}/projects/{state['project_id']}")
    assert r.status_code == 404


def test_projects_unauthenticated():
    r = requests.get(f"{API}/projects")
    assert r.status_code == 401


# ---------- UPLOAD REFERENCE ----------
def test_upload_reference(session):
    jpeg_bytes = _make_jpeg_with_features(seed=1)
    files = {"file": ("ref.jpg", jpeg_bytes, "image/jpeg")}
    r = session.post(f"{API}/projects/{state['project_id']}/reference", files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["mime"] == "image/jpeg"


def test_get_reference_image(session):
    r = session.get(f"{API}/projects/{state['project_id']}/reference-image")
    assert r.status_code == 200
    data = r.json()
    assert data["data_url"].startswith("data:image/jpeg;base64,")


def test_upload_reference_bad_format(session):
    files = {"file": ("evil.bmp", b"BM\x00\x00\x00", "image/bmp")}
    r = session.post(f"{API}/projects/{state['project_id']}/reference", files=files)
    assert r.status_code == 400


# ---------- UPLOAD CATALOGUE ----------
def test_upload_catalogue_images(session):
    bytes1 = _make_jpeg_with_features(seed=2)
    bytes2 = _make_jpeg_with_features(seed=3)
    files = [
        ("files", ("cat1.jpg", bytes1, "image/jpeg")),
        ("files", ("cat2.jpg", bytes2, "image/jpeg")),
    ]
    r = session.post(f"{API}/projects/{state['project_id']}/catalogue", files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 2
    assert len(data["items"]) == 2


def test_get_catalogue_item(session):
    r = session.get(f"{API}/projects/{state['project_id']}/catalogue/0")
    assert r.status_code == 200
    assert r.json()["data_url"].startswith("data:image/")


def test_get_catalogue_item_out_of_range(session):
    r = session.get(f"{API}/projects/{state['project_id']}/catalogue/99")
    assert r.status_code == 404


# ---------- ANALYZE ----------
def test_start_analysis(session):
    r = session.post(f"{API}/projects/{state['project_id']}/analyze",
                     data={"prompt": "Focus on wood textures and warm tones."})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"


def test_poll_status_until_complete(session):
    deadline = time.time() + 240  # up to 4 min for 2 catalogue items
    final = None
    while time.time() < deadline:
        r = session.get(f"{API}/projects/{state['project_id']}/status")
        assert r.status_code == 200
        d = r.json()
        if d["status"] in ("completed", "error"):
            final = d
            break
        time.sleep(4)
    assert final is not None, "Analysis did not finish within timeout"
    if final["status"] == "error":
        pytest.fail(f"Analysis failed: {final.get('error')}")
    assert final["status"] == "completed"


def test_get_project_with_analysis(session):
    r = session.get(f"{API}/projects/{state['project_id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "completed"
    analysis = data.get("analysis")
    assert analysis is not None
    assert "summary" in analysis
    assert isinstance(analysis.get("materials"), list)
    assert isinstance(analysis.get("matches"), list)
    assert len(analysis["matches"]) == 2


def test_analyze_without_reference():
    s2 = requests.Session()
    email = f"empty_{uuid.uuid4().hex[:6]}@t.com"
    s2.post(f"{API}/auth/register",
            json={"email": email, "password": "Pass2026!", "name": "E"})
    pr = s2.post(f"{API}/projects", json={"name": "TEST_Empty"})
    pid = pr.json()["id"]
    r = s2.post(f"{API}/projects/{pid}/analyze", data={"prompt": ""})
    assert r.status_code == 400


# ---------- REPORTS ----------
def test_list_reports(session):
    r = session.get(f"{API}/reports")
    assert r.status_code == 200
    data = r.json()
    assert any(rep.get("project_id") == state["project_id"] for rep in data)


# ---------- LOGOUT ----------
def test_logout(session):
    r = session.post(f"{API}/auth/logout")
    assert r.status_code == 200
    # Subsequent /me should fail
    r2 = session.get(f"{API}/auth/me")
    assert r2.status_code == 401


# ---------- DELETE PROJECT (cleanup) ----------
def test_cleanup_delete_project():
    s2 = requests.Session()
    s2.post(f"{API}/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    r = s2.delete(f"{API}/projects/{state['project_id']}")
    assert r.status_code == 200
