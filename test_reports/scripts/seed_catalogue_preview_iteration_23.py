#!/usr/bin/env python3
"""Seed a focused project for catalogue Preview button UI verification.

Creates/registers a regular (non-admin) user, a project with a tiny reference image,
and stores mock_analysis rows that exercise both preview rendering branches:
- recommended-preview-btn-* with swatch_crop_b64 -> catalogue-preview-image
- catalogue-preview-btn-* with color_hex only -> catalogue-preview-hex
No product code is modified.
"""
import base64
import io
import json
import os
import time
from pathlib import Path

import requests
from PIL import Image
from pymongo import MongoClient
from bson import ObjectId

BASE = os.environ.get("BASE", "http://localhost:8001/api")
OUT = Path("/app/test_reports/catalogue_preview_seed_iteration_23.json")


def tiny_jpeg_b64(color):
    img = Image.new("RGB", (32, 32), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def main():
    sess = requests.Session()
    suffix = int(time.time())
    email = f"qa-catalogue-preview-iter23-{suffix}@materialmatch.ai"
    password = "Designer2026!"
    report = {"email": email, "password": password, "base": BASE, "steps": [], "violations": []}

    def step(name, ok, data=None):
        report["steps"].append({"name": name, "ok": bool(ok), "data": data or {}})
        print(f"{'PASS' if ok else 'FAIL'}: {name} {data or ''}")

    r = sess.post(f"{BASE}/auth/register", json={"email": email, "password": password, "name": "QA Catalogue Preview"}, timeout=30)
    step("register regular user", r.status_code == 200, {"status": r.status_code, "role": r.json().get("role") if r.ok else None})
    r.raise_for_status()
    token = r.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}

    r = sess.post(f"{BASE}/projects", json={"name": "QA Catalogue Preview Iter 23", "client_name": "QA"}, headers=headers, timeout=30)
    step("create project", r.status_code == 200, {"status": r.status_code})
    r.raise_for_status()
    project_id = r.json()["id"]
    report["project_id"] = project_id

    # Upload a tiny real image so Analysis page has a reference pane.
    image_bytes = base64.b64decode(tiny_jpeg_b64((240, 235, 224)))
    r = sess.post(
        f"{BASE}/projects/{project_id}/reference",
        files={"file": ("preview-seed.jpg", image_bytes, "image/jpeg")},
        headers=headers,
        timeout=30,
    )
    step("upload reference image", r.status_code == 200, {"status": r.status_code})
    r.raise_for_status()

    swatch_b64 = tiny_jpeg_b64((120, 82, 55))
    mock_analysis = {
        "version": "qa-preview-seed-v1",
        "generated_at": "2026-07-19T00:00:00Z",
        "summary": {"design_style": "QA seeded", "material_palette": "brown, ivory", "key_finishes": "matte"},
        "rows": [
            {
                "zone": "Wall · Preview QA",
                "group": "Wall",
                "object_type": "wall",
                "material_family": "Paint",
                "material_type": "matte wall paint",
                "color": "warm ivory",
                "texture": "smooth",
                "finish": "matte",
                "confidence": 92,
                "pin": {"x": 50, "y": 45},
                "pin_source": "qa_seed",
                "catalogue_matches": [
                    {
                        "id": "qa-rec-swatch",
                        "brand": "QA Paints",
                        "catalogue": "Preview Button Test",
                        "material_name": "Brown Swatch Image",
                        "material_code": "QA-SWATCH-1",
                        "page_number": 3,
                        "material_family": "Paint",
                        "category": "Paints",
                        "finish": "Matte",
                        "color_name": "Dark brown",
                        "color_hex": "#785237",
                        "source_library": "Published Library",
                        "match_percent": 91,
                        "match_reason": "Seeded match with swatch_crop_b64 for image preview branch.",
                        "swatch_crop_b64": swatch_b64,
                        "source_page_href": "/api/admin/studio/uploads/qa-upload/page/3",
                        "similarity": {"visual": 91, "color": 89, "finish": 90, "texture": 80},
                    },
                    {
                        "id": "qa-alt-hex",
                        "brand": "QA Paints",
                        "catalogue": "Preview Button Test",
                        "material_name": "Ivory Hex Only",
                        "material_code": "QA-HEX-2",
                        "page_number": 4,
                        "material_family": "Paint",
                        "category": "Paints",
                        "finish": "Matte",
                        "color_name": "Ivory",
                        "color_hex": "#F1EBE0",
                        "source_library": "Published Library",
                        "match_percent": 86,
                        "match_reason": "Seeded match without swatch_crop_b64 for hex preview branch.",
                        "swatch_crop_b64": None,
                        "source_page_href": "/api/admin/studio/uploads/qa-upload/page/4",
                        "similarity": {"visual": 80, "color": 94, "finish": 90, "texture": 75},
                    },
                ],
                "match_buckets": {
                    "best": [],
                    "possible": [],
                    "low": [],
                },
            }
        ],
    }
    mock_analysis["rows"][0]["match_buckets"]["best"] = mock_analysis["rows"][0]["catalogue_matches"]

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "test_database")
    client = MongoClient(mongo_url)
    db = client[db_name]
    res = db.projects.update_one({"_id": ObjectId(project_id)}, {"$set": {"mock_analysis": mock_analysis, "status": "completed"}})
    step("seed mock_analysis with catalogue matches", res.modified_count == 1, {"modified_count": res.modified_count})

    r = sess.get(f"{BASE}/projects/{project_id}", headers=headers, timeout=30)
    step("GET project returns seeded rows", r.status_code == 200 and len(((r.json().get("mock_analysis") or {}).get("rows") or [])) == 1, {"status": r.status_code})

    report["passed"] = all(s["ok"] for s in report["steps"]) and not report["violations"]
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
