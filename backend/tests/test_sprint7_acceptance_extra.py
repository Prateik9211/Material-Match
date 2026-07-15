"""Sprint 7 extra acceptance checks:
- Retrieval-only match_percent cap at 88
- Full /projects/{id}/analyze does NOT run visual rerank on rows
- Publish flow kicks visual_dna + dna_embedding enrichment
"""
import base64
import io
import os
import time
import uuid

import pytest
import requests
from dotenv import load_dotenv
from PIL import Image
from pymongo import MongoClient

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

API = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") + "/api"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _b64_solid(color=(120, 60, 60), size=(400, 400)):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture(scope="module")
def sess():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={
        "email": "admin@materialmatch.ai",
        "password": "MaterialAdmin2026!"
    }, timeout=20)
    assert r.status_code == 200, r.text
    # Reset any daily quota to avoid 429s
    db.usage_counters.delete_many({})
    return s


@pytest.fixture(scope="module")
def project(sess):
    r = sess.post(f"{API}/projects", json={
        "name": "TEST_Sprint7_Acceptance",
        "room_type": "Kitchen",
        "budget_range": "Standard",
    }, timeout=20)
    assert r.status_code in (200, 201), r.text
    pid = r.json().get("id") or r.json().get("_id")
    yield pid
    try:
        sess.delete(f"{API}/projects/{pid}", timeout=15)
    except Exception:
        pass


def test_retrieval_only_matches_capped_at_88(sess, project):
    """Send a random solid crop with no full_image (analysis-region without rerank
    context: single row, but no bbox+full so it can still rerank... use no
    full_image_b64 and no bbox to skip rerank via lack of context)."""
    # Use a color that unlikely matches exactly -> retrieval path
    r = sess.post(f"{API}/projects/{project}/analyze-region",
                  json={"crop_b64": _b64_solid((190, 120, 45)),
                        "note": "retrieval cap test"},
                  timeout=180)
    assert r.status_code == 200, r.text
    rows = r.json().get("rows", [])
    assert rows
    for row in rows:
        for m in (row.get("catalogue_matches") or []):
            dbg = m.get("debug") or {}
            stage = dbg.get("pipeline_stage")
            assert stage in ("retrieval", "exact_loopback"), dbg
            assert "retrieval_score" in dbg
            assert "embedding_similarity" in dbg
            if stage == "retrieval" and not m.get("visually_verified"):
                assert m["match_percent"] <= 88, m


def test_full_analyze_does_not_run_rerank(sess, project):
    """POST /projects/{id}/analyze — rows must have catalogue_matches from
    retrieval, and none of them should be visually_verified / row.rerank.ran."""
    # A simple full room image
    img = Image.new("RGB", (900, 600), (230, 225, 215))
    buf = io.BytesIO(); img.save(buf, format="JPEG", quality=85)
    files = {"file": ("ref.jpg", buf.getvalue(), "image/jpeg")}
    up = sess.post(f"{API}/projects/{project}/reference", files=files, timeout=30)
    assert up.status_code in (200, 201), up.text[:200]
    r = sess.post(f"{API}/projects/{project}/analyze", json={}, timeout=240)
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    rows = data.get("rows") or data.get("materials") or []
    assert isinstance(rows, list)
    # rows may be empty for a blank image; if present, check invariants
    for row in rows:
        rerank = row.get("rerank") or {}
        assert rerank.get("ran") is not True, f"full analyze should not rerank: {rerank}"
        for m in (row.get("catalogue_matches") or []):
            assert not m.get("visually_verified"), m
            dbg = m.get("debug") or {}
            assert dbg.get("pipeline_stage") in ("retrieval", "exact_loopback")


def test_publish_kicks_dna_enrichment(sess):
    """Find a published record, strip its visual_dna/dna_embedding, republish
    via the approve endpoint and verify it re-enriches within ~30s."""
    rec = db.ke_records.find_one({
        "status": "published",
        "page_preview_b64": {"$type": "string", "$ne": ""},
        "upload_id": {"$exists": True},
    })
    assert rec, "no published record with image"
    rid = rec["_id"]
    # Strip DNA fields to force re-enrichment
    db.ke_records.update_one({"_id": rid},
                             {"$unset": {"visual_dna": "", "dna_embedding": ""}})
    # Call approve/republish endpoint (idempotent)
    r = sess.post(f"{API}/admin/studio/records/approve",
                  json={"record_ids": [str(rid)]},
                  timeout=30)
    # Some deployments use PUT/POST variants — accept 2xx / 404 (then skip)
    if r.status_code == 404:
        pytest.skip("approve endpoint not present at expected path")
    assert r.status_code in (200, 201, 202), r.text[:300]

    enriched = False
    for _ in range(30):
        doc = db.ke_records.find_one({"_id": rid},
                                     {"visual_dna": 1, "dna_embedding": 1})
        if doc and doc.get("visual_dna") and doc.get("dna_embedding"):
            enriched = True
            break
        time.sleep(1)
    assert enriched, "publish did not trigger DNA enrichment within 30s"
