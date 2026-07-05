"""RC1 verification — demo trust rules + curated matches + KE routing"""
import os, requests, pytest

BASE_URL = (os.environ.get('REACT_APP_BACKEND_URL') or
            'https://design-match-ai.preview.emergentagent.com').rstrip('/')
ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASSWORD = "MaterialAdmin2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"admin login failed: {r.text}"
    return s


@pytest.fixture(scope="module")
def demo():
    r = requests.get(f"{BASE_URL}/api/demo/project")
    assert r.status_code == 200
    return r.json()


class TestDemoTrustRules:
    def test_ten_curated_zones(self, demo):
        rows = demo.get("mock_analysis", {}).get("rows", [])
        assert len(rows) == 10

    def test_nine_zones_have_three_matches(self, demo):
        rows = demo["mock_analysis"]["rows"]
        counts = [len(r.get("catalogue_matches", [])) for r in rows]
        assert counts.count(3) == 9, f"expected 9 zones with 3 matches, got {counts}"

    def test_foliage_zone_zero_matches(self, demo):
        rows = demo["mock_analysis"]["rows"]
        foliage = [r for r in rows if "Foliage" in r.get("zone", "")]
        assert len(foliage) == 1
        assert len(foliage[0].get("catalogue_matches", [])) == 0

    def test_no_fabricated_mm_demo_codes(self, demo):
        assert "MM-DEMO" not in str(demo)

    def test_match_percent_range(self, demo):
        rows = demo["mock_analysis"]["rows"]
        for r in rows:
            for m in r.get("catalogue_matches", []):
                pct = m.get("match_percent") or m.get("score") or 0
                assert 78 <= pct <= 94, f"{r['zone']}: {m.get('brand')} pct={pct} out of 78-94"

    def test_real_brands(self, demo):
        rows = demo["mock_analysis"]["rows"]
        for r in rows:
            for m in r.get("catalogue_matches", []):
                assert m.get("brand"), f"{r['zone']}: match missing brand"
                assert m.get("material_name"), f"{r['zone']}: match missing name"

    def test_reference_image(self):
        r = requests.get(f"{BASE_URL}/api/demo/reference-image")
        assert r.status_code == 200
        assert r.json().get("data_url", "").startswith("data:image/")


class TestKnowledgeEngineRouting:
    def test_warm_ivory_returns_paints_first(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/knowledge-engine",
                              params={"q": "Warm Ivory", "limit": 10})
        assert r.status_code == 200
        recs = r.json().get("records", [])
        assert recs, "no records"
        top = recs[0]
        assert top["brand"] == "Asian Paints"
        assert top["material_name"] == "Warm Ivory"

    def test_fluted_returns_real_records_no_paints(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/knowledge-engine",
                              params={"q": "Fluted", "limit": 20})
        recs = r.json().get("records", [])
        assert any(x["brand"] == "Greenlam" and "Fluted" in x["material_name"]
                   for x in recs)
        # No Paint category
        paints = [x for x in recs if x.get("category") == "Paints"]
        assert not paints, f"Paints leaked into Fluted search: {paints}"


class TestDesignerLiveWorkflow:
    def test_designer_can_login_or_register(self):
        s = requests.Session()
        r = s.post(f"{BASE_URL}/api/auth/login",
                   json={"email": "designer@test.com", "password": "Designer2026!"})
        if r.status_code != 200:
            r = s.post(f"{BASE_URL}/api/auth/register",
                       json={"email": "designer@test.com", "password": "Designer2026!",
                             "name": "RC Designer"})
        assert r.status_code == 200
        me = s.get(f"{BASE_URL}/api/auth/me").json()
        assert me["email"] == "designer@test.com"
