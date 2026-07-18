"""E2E test for pin fallback + side-by-side layout regression."""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://design-match-ai.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASSWORD = "MaterialAdmin2026!"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text[:200]}"
    return s


def test_login_and_me(session):
    r = session.get(f"{BASE_URL}/api/auth/me", timeout=15)
    assert r.status_code == 200
    assert r.json().get("email") == ADMIN_EMAIL


def test_list_projects_and_find_analyzed(session):
    r = session.get(f"{BASE_URL}/api/projects", timeout=30)
    assert r.status_code == 200
    projects = r.json()
    assert isinstance(projects, list) and len(projects) > 0
    print(f"Admin has {len(projects)} projects")
    # Find a project with mock_analysis rows
    analyzed = None
    for p in projects:
        # projects list might be summarized — pull full detail
        pid = p.get("id")
        if not pid:
            continue
        r2 = session.get(f"{BASE_URL}/api/projects/{pid}", timeout=15)
        if r2.status_code != 200:
            continue
        full = r2.json()
        rows = (full.get("mock_analysis") or {}).get("rows") or []
        if rows:
            analyzed = full
            print(f"Found analyzed project {pid} with {len(rows)} rows")
            break
    # Store on module for reuse
    pytest.analyzed_project = analyzed
    assert analyzed is not None, "No analyzed project found for admin"


def test_every_row_has_pin(session):
    proj = getattr(pytest, "analyzed_project", None)
    if not proj:
        pytest.skip("no analyzed project available")
    # Force a fresh analyze so we validate the CURRENT code path (fallback + pin_source tagging)
    pid = proj["id"]
    print(f"Forcing fresh analyze on project {pid}")
    r = session.post(f"{BASE_URL}/api/projects/{pid}/analyze", timeout=240)
    assert r.status_code == 200, f"analyze failed: {r.status_code} {r.text[:300]}"
    r2 = session.get(f"{BASE_URL}/api/projects/{pid}", timeout=15)
    proj = r2.json()
    rows = proj["mock_analysis"]["rows"]
    assert rows, "analyze produced zero rows"
    # After re-analyze — every row MUST have a pin
    for i, r in enumerate(rows):
        assert r.get("pin"), f"Row {i} ({r.get('zone')}) is missing pin"
        assert isinstance(r["pin"].get("x"), (int, float)), f"Row {i} pin.x invalid"
        assert isinstance(r["pin"].get("y"), (int, float)), f"Row {i} pin.y invalid"
        # pin_source must be tagged
        assert r.get("pin_source") in ("llm", "fallback_group"), f"Row {i} unexpected pin_source={r.get('pin_source')}"
    # Count sources
    sources = {}
    for r in rows:
        sources[r.get("pin_source")] = sources.get(r.get("pin_source"), 0) + 1
    print(f"pin_source counts: {sources}")


def test_analyze_region_single_mode(session):
    """Regression: default single-mode analyze-region still works (needs crop_b64)."""
    proj = getattr(pytest, "analyzed_project", None)
    if not proj:
        pytest.skip("no analyzed project")
    pid = proj["id"]
    # Fetch reference image → build a base64 crop of a sub-region
    import base64, io
    from PIL import Image
    ref_b64 = proj.get("reference_image_b64")
    if not ref_b64:
        pytest.skip("project has no reference_image_b64")
    img_bytes = base64.b64decode(ref_b64)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    crop = img.crop((int(w*0.3), int(h*0.3), int(w*0.7), int(h*0.7)))
    buf = io.BytesIO(); crop.save(buf, format="JPEG", quality=85)
    crop_b64 = base64.b64encode(buf.getvalue()).decode()
    payload = {
        "crop_b64": crop_b64,
        "rect": {"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.4},
    }
    r = session.post(f"{BASE_URL}/api/projects/{pid}/analyze-region", json=payload, timeout=240)
    print(f"analyze-region status={r.status_code} preview={r.text[:200]}")
    assert r.status_code == 200, f"analyze-region single failed: {r.status_code} {r.text[:300]}"
    body = r.json()
    assert "rows" in body and isinstance(body["rows"], list)
