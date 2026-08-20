"""Tests for Reviews + Admin Users features (2026-02 sprint)."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://design-match-ai.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PW = "MaterialAdmin2026!"

# Use a unique non-admin user with a "test" pattern email — should be filtered
NON_ADMIN_EMAIL = f"designer@test.com"
NON_ADMIN_PW = "Designer2026!"


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    if r.status_code != 200:
        # try register first
        rr = requests.post(f"{API}/auth/register",
                           json={"email": email, "password": password, "name": email.split("@")[0]},
                           timeout=20)
        if rr.status_code in (200, 201):
            tok = rr.json().get("access_token")
            if tok:
                return tok
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def admin_token():
    return _login(ADMIN_EMAIL, ADMIN_PW)


@pytest.fixture(scope="module")
def user_token():
    return _login(NON_ADMIN_EMAIL, NON_ADMIN_PW)


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


# -------------------- Reviews: POST /api/reviews --------------------
class TestPostReview:
    def test_valid_submission(self, user_token):
        r = requests.post(f"{API}/reviews", headers=_h(user_token),
                          json={"rating": 5, "comment": "Great product from tester", "role": "Interior Designer"},
                          timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["approved"] is True
        assert d["rating"] == 5
        assert d["comment"] == "Great product from tester"
        assert d["role"] == "Interior Designer"

    def test_invalid_rating_high(self, user_token):
        r = requests.post(f"{API}/reviews", headers=_h(user_token),
                          json={"rating": 7, "comment": "bad"}, timeout=15)
        assert r.status_code == 400
        assert "rating must be 1-5" in r.text.lower()

    def test_invalid_rating_low(self, user_token):
        r = requests.post(f"{API}/reviews", headers=_h(user_token),
                          json={"rating": 0, "comment": "bad"}, timeout=15)
        assert r.status_code == 400

    def test_empty_comment(self, user_token):
        r = requests.post(f"{API}/reviews", headers=_h(user_token),
                          json={"rating": 4, "comment": "   "}, timeout=15)
        assert r.status_code == 400
        assert "comment is required" in r.text.lower()

    def test_unauthenticated(self):
        r = requests.post(f"{API}/reviews",
                          json={"rating": 3, "comment": "no auth"}, timeout=15)
        assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"


# -------------------- Admin reviews --------------------
class TestAdminReviews:
    def test_list_as_admin(self, admin_token):
        r = requests.get(f"{API}/admin/reviews", headers=_h(admin_token), timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "reviews" in d and "count" in d
        assert isinstance(d["reviews"], list)
        assert d["count"] == len(d["reviews"])
        # sorted most recent first
        if len(d["reviews"]) >= 2:
            ts = [rv["created_at"] for rv in d["reviews"] if rv.get("created_at")]
            assert ts == sorted(ts, reverse=True)

    def test_list_as_non_admin_forbidden(self, user_token):
        r = requests.get(f"{API}/admin/reviews", headers=_h(user_token), timeout=15)
        assert r.status_code == 403

    def test_toggle_review(self, admin_token, user_token):
        # create fresh review as user
        cr = requests.post(f"{API}/reviews", headers=_h(user_token),
                           json={"rating": 4, "comment": "toggle test"}, timeout=15)
        assert cr.status_code == 200
        rid = cr.json()["id"]

        # toggle to approved
        pr = requests.patch(f"{API}/admin/reviews/{rid}", headers=_h(admin_token),
                            json={"approved": True}, timeout=15)
        assert pr.status_code == 200, pr.text
        assert pr.json()["approved"] is True

        # persistence check
        lst = requests.get(f"{API}/admin/reviews", headers=_h(admin_token), timeout=15).json()
        match = next((x for x in lst["reviews"] if x["id"] == rid), None)
        assert match is not None and match["approved"] is True

    def test_toggle_as_non_admin_forbidden(self, user_token):
        r = requests.patch(f"{API}/admin/reviews/nonexistent",
                           headers=_h(user_token),
                           json={"approved": True}, timeout=15)
        assert r.status_code == 403

    def test_toggle_unknown_id(self, admin_token):
        r = requests.patch(f"{API}/admin/reviews/DOES_NOT_EXIST_zzz",
                           headers=_h(admin_token),
                           json={"approved": True}, timeout=15)
        assert r.status_code == 404


# -------------------- Admin users filter --------------------
BAD_DOMAINS = ("@test.com", "@t.com", "@example.com", "@materialmatch.ai")
BAD_PREFIXES = ("test_", "uitest_", "sam3_", "sprint", "region_pref_", "other_", "empty_", "qa")


class TestAdminUsersFilter:
    def test_as_admin_returns_2(self, admin_token):
        r = requests.get(f"{API}/admin/users", headers=_h(admin_token), timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        emails = [u["email"] for u in d["users"]]
        print(f"REAL USERS ({d['count']}): {emails}")
        assert d["count"] == 2, f"expected 2 real users, got {d['count']}: {emails}"
        assert set(emails) == {"pgirwalkar@gmail.com", "ar.priyankasg@gmail.com"}

    def test_no_test_pattern_in_users_list(self, admin_token):
        r = requests.get(f"{API}/admin/users", headers=_h(admin_token), timeout=20)
        assert r.status_code == 200
        for u in r.json()["users"]:
            em = (u["email"] or "").lower()
            for bad in BAD_DOMAINS:
                assert not em.endswith(bad), f"leaked bad domain: {em}"
            local = em.split("@")[0]
            for pref in BAD_PREFIXES:
                assert not local.startswith(pref), f"leaked bad prefix {pref}: {em}"

    def test_as_non_admin_forbidden(self, user_token):
        r = requests.get(f"{API}/admin/users", headers=_h(user_token), timeout=15)
        assert r.status_code == 403


class TestAdminStats:
    def test_stats(self, admin_token):
        r = requests.get(f"{API}/admin/stats", headers=_h(admin_token), timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "total_users" in d and "real_users" in d
        print(f"STATS: {d}")
        # Post-purge (2026-02-14): real_users kept at 2 canonical humans,
        # total_users floats with fresh test-user creation but must be
        # >= real_users at all times.
        assert d["real_users"] >= 2
        assert d["total_users"] >= d["real_users"]
