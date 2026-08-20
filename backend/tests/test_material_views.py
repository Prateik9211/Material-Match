"""Tests for the Material View backfill endpoints (2026-02-14)."""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL",
                          "https://design-match-ai.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PW = "MaterialAdmin2026!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PW}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


class TestStatus:
    def test_status_shape(self, admin_token):
        r = requests.get(f"{API}/admin/material-views/status", headers=_h(admin_token), timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "coverage" in d and "progress" in d
        c = d["coverage"]
        assert set(c.keys()) == {"published_with_swatch", "with_material_view", "missing"}
        p = d["progress"]
        assert "running" in p and "processed" in p and "generated" in p

    def test_forbidden_for_non_admin(self):
        # Create a fresh non-admin
        email = "matview_nonadmin@test.com"
        pw = "MatView2026!"
        r = requests.post(f"{API}/auth/register",
                          json={"email": email, "password": pw, "name": "MV NA"}, timeout=15)
        if r.status_code not in (200, 201):
            r = requests.post(f"{API}/auth/login",
                              json={"email": email, "password": pw}, timeout=15)
        tok = r.json()["access_token"]
        rr = requests.get(f"{API}/admin/material-views/status", headers=_h(tok), timeout=10)
        assert rr.status_code == 403


class TestBackfill:
    def test_kick_backfill_idempotent(self, admin_token):
        # If a run is in flight the API returns queued:false rather than crashing.
        r = requests.post(f"{API}/admin/material-views/backfill",
                          headers=_h(admin_token), timeout=10)
        assert r.status_code == 200
        d = r.json()
        # Either queued (fresh run) or a graceful no-op if already running.
        assert "queued" in d
        assert isinstance(d["queued"], bool)
