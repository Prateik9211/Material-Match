"""Integration test for the /similar endpoint.

Talks to the LIVE running FastAPI backend on localhost:8001 to avoid
motor + pytest-asyncio event-loop scoping issues. Mocks the real SerpApi
call by pre-seeding cache entries, so no real search credit is spent.
"""
import base64
import io
import os
import sys
import time

import pytest
import requests
from PIL import Image
from pymongo import MongoClient
from bson import ObjectId

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

BASE = "http://localhost:8001"
_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]


def _make_ref_image_b64() -> str:
    img = Image.new("RGB", (1000, 1000), (200, 180, 150))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def _seed_project(user_id: str, products: list) -> str:
    from datetime import datetime, timezone
    r = _db.projects.insert_one({
        "user_id": user_id,
        "name": "PS-Test",
        "status": "analyzed",
        "reference_image_b64": _make_ref_image_b64(),
        "products_detected": {"products": products},
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    })
    return str(r.inserted_id)


def _clean(pid: str | None = None):
    if pid:
        _db.projects.delete_one({"_id": ObjectId(pid)})
    _db.product_search_cache.delete_many({})
    _db.product_search_crops.delete_many({})
    _db.product_search_usage.delete_many({})


def _jwt_for(uid: str, email: str) -> str:
    import jwt
    from datetime import datetime, timedelta
    return jwt.encode({
        "sub": uid, "email": email, "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=30),
    }, os.environ["JWT_SECRET"], algorithm="HS256")


def _preseed_cache(cache_key: str, similar_items: list):
    """Pretend a previous SerpApi call already ran — the endpoint returns
    the cached value instead of calling SerpApi."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    _db.product_search_cache.update_one(
        {"_id": cache_key},
        {"$set": {
            "similar_items": similar_items,
            "raw_match_count": len(similar_items),
            "country": "in",
            "elapsed_s": 0.0,
            "crop_sha": cache_key.split("_")[0],
            "fetched_at": now,
            "expires_at": now + timedelta(days=30),
        }},
        upsert=True,
    )


def _compute_cache_key_for(product: dict) -> str:
    """Recreate the crop bytes exactly like the endpoint does, then sha256."""
    from intelligence.product_search import prepare_crop_bytes, crop_cache_key
    img = Image.new("RGB", (1000, 1000), (200, 180, 150))
    b = prepare_crop_bytes(img, product["sam3_bbox"])
    return crop_cache_key(b, country="in")


def _admin():
    u = _db.users.find_one({"email": "admin@materialmatch.ai"})
    if not u:
        pytest.skip("admin user not seeded")
    return u


def test_gate_skipped_returns_reason_no_api_call():
    u = _admin()
    uid = str(u["_id"])
    _clean()
    pid = _seed_project(uid, [{
        "id": "product_1", "product_name": "Pendant Light",
        "category": "lighting", "confidence": 60,
        "sam3_bbox": [10, 10, 60, 800], "sam3_confidence": 0.7,  # aspect ~13
    }])
    try:
        token = _jwt_for(uid, u["email"])
        r = requests.post(
            f"{BASE}/api/projects/{pid}/products/product_1/similar",
            headers={"Authorization": f"Bearer {token}"}, json={}, timeout=15,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["gate"]["passed"] is False
        assert "aspect_ratio" in d["gate"]["reason"]
        assert d["quota_used_this_month"] == 0
        assert d["similar_items"] == []
    finally:
        _clean(pid)


def test_cache_hit_returns_previous_result_without_spending_quota():
    """Pre-seed the cache; endpoint MUST return it and NOT increment quota."""
    u = _admin()
    uid = str(u["_id"])
    _clean()
    prod = {
        "id": "product_1", "product_name": "Armchair", "category": "furniture",
        "confidence": 82,
        "sam3_bbox": [100, 100, 400, 250], "sam3_confidence": 0.85,
    }
    pid = _seed_project(uid, [prod])
    try:
        cache_key = _compute_cache_key_for(prod)
        _preseed_cache(cache_key, [
            {"title": "Preseeded Armchair", "source": "Amazon.in",
             "price_display": "\u20b912,999", "price_value": 12999,
             "currency": "INR", "link": "https://www.amazon.in/dp/FAKE",
             "thumbnail": ""},
        ])
        token = _jwt_for(uid, u["email"])
        r = requests.post(
            f"{BASE}/api/projects/{pid}/products/product_1/similar",
            headers={"Authorization": f"Bearer {token}"}, json={}, timeout=15,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["gate"]["passed"] is True
        assert d["cached"] is True
        assert d["quota_used_this_month"] == 0, "cache hit must NOT touch quota"
        assert len(d["similar_items"]) == 1
        assert d["similar_items"][0]["source"] == "Amazon.in"
        assert d["similar_items"][0]["title"] == "Preseeded Armchair"
    finally:
        _clean(pid)


def test_quota_exhaustion_returns_empty_without_api_call():
    """When we're at the monthly cap, no API call and empty result."""
    from datetime import datetime, timezone
    u = _admin()
    uid = str(u["_id"])
    _clean()
    key = datetime.now(timezone.utc).strftime("%Y-%m")
    # Set counter to the cap (must read cap from server module)
    import server
    _db.product_search_usage.update_one(
        {"_id": key},
        {"$set": {"count": server.PRODUCT_SEARCH_MONTHLY_CAP,
                  "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    pid = _seed_project(uid, [{
        "id": "product_1", "product_name": "Armchair", "category": "furniture",
        "confidence": 82,
        "sam3_bbox": [100, 100, 400, 250], "sam3_confidence": 0.85,
    }])
    try:
        token = _jwt_for(uid, u["email"])
        r = requests.post(
            f"{BASE}/api/projects/{pid}/products/product_1/similar",
            headers={"Authorization": f"Bearer {token}"}, json={}, timeout=15,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["quota_exhausted"] is True
        assert d["similar_items"] == []
        assert d["error"] == "monthly_quota_exhausted"
    finally:
        _clean(pid)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
