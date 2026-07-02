"""Sprint 2 integration tests: admin CRUD, product detection, permissions."""
import os
import io
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://design-match-ai.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASSWORD = "MaterialAdmin2026!"
USER_EMAIL = "designer@test.com"
USER_PASSWORD = "Designer2026!"


def _login_or_register(email, password, name="User"):
    s = requests.Session()
    # try login
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    if r.status_code != 200:
        # try register
        s.post(f"{API}/auth/register", json={"email": email, "password": password, "name": name}, timeout=15)
        r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text}"
    token = r.json().get("access_token") or r.json().get("token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s, r.json()


@pytest.fixture(scope="module")
def admin_session():
    s, data = _login_or_register(ADMIN_EMAIL, ADMIN_PASSWORD, "Admin")
    return s


@pytest.fixture(scope="module")
def user_session():
    s, data = _login_or_register(USER_EMAIL, USER_PASSWORD, "Test Designer")
    return s


# ---------- Auth / role checks ----------
def test_admin_me_is_admin(admin_session):
    r = admin_session.get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    assert r.json().get("role") == "admin", r.json()


def test_user_me_is_not_admin(user_session):
    r = user_session.get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    assert r.json().get("role") != "admin"


# ---------- Admin affiliates endpoints ----------
def test_list_affiliates_unauthenticated_401():
    r = requests.get(f"{API}/admin/affiliates", timeout=15)
    assert r.status_code in (401, 403)


def test_list_affiliates_as_user_403(user_session):
    r = user_session.get(f"{API}/admin/affiliates", timeout=15)
    assert r.status_code == 403


def test_list_affiliates_as_admin_returns_seed(admin_session):
    r = admin_session.get(f"{API}/admin/affiliates", timeout=15)
    assert r.status_code == 200
    data = r.json()
    items = data if isinstance(data, list) else data.get("items") or data.get("affiliates") or []
    assert len(items) >= 10, f"Expected >=10 seeded affiliates, got {len(items)}"
    platforms = {(it.get("platform") or "").lower() for it in items}
    # At least one Indian platform present
    indian = {"pepperfry", "urban ladder", "ikea india", "woodenstreet", "hafele india", "amazon india", "jaipur rugs"}
    assert platforms & indian, f"No Indian platforms found: {platforms}"


def test_admin_create_update_delete_affiliate(admin_session):
    payload = {
        "product_name": f"TEST_Lamp_{uuid.uuid4().hex[:6]}",
        "product_category": "lighting",
        "platform": "Pepperfry",
        "affiliate_url": "https://example.com/test-lamp",
        "style_keywords": ["modern", "warm"],
        "price_inr": "₹4,999",
    }
    r = admin_session.post(f"{API}/admin/affiliates", json=payload, timeout=15)
    assert r.status_code in (200, 201), f"create failed: {r.status_code} {r.text}"
    created = r.json()
    aid = created.get("id") or created.get("_id")
    assert aid, f"no id in {created}"

    # Update
    upd = admin_session.put(f"{API}/admin/affiliates/{aid}", json={"price_inr": "₹5,999"}, timeout=15)
    assert upd.status_code == 200, f"update failed: {upd.status_code} {upd.text}"
    assert "5,999" in (upd.json().get("price_inr") or "")

    # Delete
    d = admin_session.delete(f"{API}/admin/affiliates/{aid}", timeout=15)
    assert d.status_code in (200, 204), f"delete failed: {d.status_code} {d.text}"


def test_admin_create_bad_category_returns_400(admin_session):
    payload = {
        "product_name": "TEST_BadCat",
        "product_category": "not_a_real_category_xyz",
        "platform": "Pepperfry",
        "affiliate_url": "https://example.com",
        "style_keywords": ["x"],
    }
    r = admin_session.post(f"{API}/admin/affiliates", json=payload, timeout=15)
    assert r.status_code in (400, 422), f"expected validation error, got {r.status_code} {r.text}"


def test_user_cannot_create_affiliate(user_session):
    payload = {
        "product_name": "TEST_ShouldFail",
        "product_category": "lighting",
        "platform": "Pepperfry",
        "affiliate_url": "https://example.com",
        "style_keywords": ["modern"],
    }
    r = user_session.post(f"{API}/admin/affiliates", json=payload, timeout=15)
    assert r.status_code == 403


# ---------- Products detection on project ----------
def _tiny_jpeg_bytes():
    # 1x1 red jpeg
    import base64
    return base64.b64decode(
        b"/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iiigD//2Q=="
    )


@pytest.fixture(scope="module")
def project_with_analysis(user_session):
    # Create project (JSON)
    r = user_session.post(f"{API}/projects", json={
        "name": f"TEST_Sprint2_{uuid.uuid4().hex[:6]}",
        "client_name": "Test Client",
        "notes": "modern living room with wooden floor and pendant lights",
    }, timeout=15)
    assert r.status_code in (200, 201), f"create project failed: {r.status_code} {r.text}"
    proj = r.json()
    pid = proj.get("id") or proj.get("_id") or proj.get("project_id")
    assert pid, f"no project id in {proj}"

    # Upload reference image
    files = {"file": ("ref.jpg", _tiny_jpeg_bytes(), "image/jpeg")}
    ur = user_session.post(f"{API}/projects/{pid}/reference", files=files, timeout=30)
    assert ur.status_code == 200, f"upload ref failed: {ur.status_code} {ur.text}"

    # Trigger analyze
    ar = user_session.post(f"{API}/projects/{pid}/analyze", timeout=120)
    assert ar.status_code == 200, f"analyze failed: {ar.status_code} {ar.text[:500]}"
    return pid, ar.json()


def test_analyze_returns_rows_and_products(project_with_analysis):
    pid, resp = project_with_analysis
    # Check rows (materials)
    assert "rows" in resp or "analysis" in resp, f"no rows: {list(resp.keys())}"
    # Check products
    products = resp.get("products")
    if products is None and "analysis" in resp:
        products = resp["analysis"].get("products")
    assert products is not None, f"no products key in response: {list(resp.keys())}"
    assert isinstance(products, list)
    assert 1 <= len(products) <= 12, f"unexpected product count: {len(products)}"
    p0 = products[0]
    for k in ("product_name", "category", "style_keywords", "search_urls"):
        assert k in p0, f"missing {k} in product: {p0}"


def test_get_products_endpoint(user_session, project_with_analysis):
    pid, _ = project_with_analysis
    r = user_session.get(f"{API}/projects/{pid}/products", timeout=15)
    assert r.status_code == 200, f"get products failed: {r.status_code} {r.text}"
    data = r.json()
    assert "products" in data
    assert "generated_at" in data or "cached_at" in data or "updated_at" in data
    assert len(data["products"]) >= 1


def test_get_products_cross_user_404(admin_session, project_with_analysis):
    pid, _ = project_with_analysis
    # Admin is a different user; should not access another user's project
    r = admin_session.get(f"{API}/projects/{pid}/products", timeout=15)
    assert r.status_code in (403, 404), f"expected 403/404 for cross-user, got {r.status_code}"


def test_products_have_search_urls(project_with_analysis):
    _, resp = project_with_analysis
    products = resp.get("products") or resp.get("analysis", {}).get("products") or []
    for p in products:
        urls = p.get("search_urls") or {}
        amazon = urls.get("amazon") or urls.get("amazon_in") or ""
        google = urls.get("google") or urls.get("google_shopping") or ""
        assert "amazon.in/s?k=" in amazon, f"bad amazon url: {amazon}"
        assert "tbm=shop" in google, f"bad google url: {google}"
