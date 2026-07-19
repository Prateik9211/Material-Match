#!/usr/bin/env python3
"""Seed a project whose analysis page has a catalogue match with swatch fields.

The browser test logs in as the created user, clicks the catalogue-match shortlist
button, then verifies whether the shortlist receives a clickable swatch lightbox.
"""
import json
import time
from pathlib import Path

import requests

BASE = "http://localhost:8001/api"
OUT = Path("/app/test_reports/frontend_lightbox_seed_iteration_20.json")


def main():
    sess = requests.Session()
    email = f"qa-lightbox-{int(time.time())}@materialmatch.ai"
    password = "Designer2026!"
    r = sess.post(f"{BASE}/auth/register", json={"email": email, "password": password, "name": "QA Lightbox"}, timeout=30)
    r.raise_for_status()
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = sess.post(f"{BASE}/projects", json={"name": "QA shortlist lightbox", "client_name": "QA"}, headers=headers, timeout=30)
    r.raise_for_status()
    project_id = r.json()["id"]

    # Upload a tiny valid JPEG reference so /reference-image succeeds.
    img = Path("/app/test_reports/real_interior_test.jpg")
    with img.open("rb") as f:
        r = sess.post(f"{BASE}/projects/{project_id}/reference", files={"file": ("ref.jpg", f, "image/jpeg")}, headers=headers, timeout=60)
    r.raise_for_status()

    # Backfill mock_analysis directly. This is seed data only; product code unchanged.
    from pymongo import MongoClient
    from bson import ObjectId
    import os
    from dotenv import load_dotenv
    load_dotenv('/app/backend/.env')
    client = MongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME'].strip('"')]
    analysis = {
        "version": "qa-seeded-lightbox-v1",
        "generated_at": "2026-07-19T00:00:00+00:00",
        "summary": {"design_style": "QA", "material_palette": "Paint", "key_finishes": "Matte", "sourcing_note": ""},
        "rows": [{
            "zone": "Wall · QA region",
            "group": "Wall",
            "object_type": "wall",
            "material_family": "Paint",
            "material_type": "Matte wall paint",
            "color": "Warm white",
            "texture": "Smooth",
            "finish": "Matte",
            "classification": "Material Surface",
            "pin": {"x": 50, "y": 40},
            "pin_source": "scene_polygon_centroid",
            "searched_libraries": ["Paint Library"],
            "searched_categories": ["Paints"],
            "brain": {"classification": "Material Surface", "allowed_categories": ["Paints"], "allowed_libraries": ["Paint Library"], "excluded_libraries": ["Laminate Library"], "object_locked": True},
            "match_buckets": {"best": [{
                "id": "qa-paint-1",
                "brand": "QA Paints",
                "catalogue": "QA shade card",
                "material_name": "Warm White 101",
                "material_code": "WW-101",
                "material_family": "Paint",
                "category": "Paints",
                "finish": "Matte",
                "color_name": "Warm white",
                "color_hex": "#F1EBE0",
                "match_percent": 88,
                "source_library": "Seeded Library",
                "match_reason": "QA seeded match with hex swatch",
                "similarity": {"visual": 88, "color": 96, "finish": 90, "texture": 80}
            }], "possible": [], "low": []},
            "catalogue_matches": [{
                "id": "qa-paint-1",
                "brand": "QA Paints",
                "catalogue": "QA shade card",
                "material_name": "Warm White 101",
                "material_code": "WW-101",
                "material_family": "Paint",
                "category": "Paints",
                "finish": "Matte",
                "color_name": "Warm white",
                "color_hex": "#F1EBE0",
                "match_percent": 88,
                "source_library": "Seeded Library",
                "match_reason": "QA seeded match with hex swatch",
                "similarity": {"visual": 88, "color": 96, "finish": 90, "texture": 80}
            }]
        }]
    }
    db.projects.update_one({"_id": ObjectId(project_id)}, {"$set": {"mock_analysis": analysis, "status": "completed", "shortlist_items": []}})
    OUT.write_text(json.dumps({"email": email, "password": password, "project_id": project_id, "analysis_url": f"http://localhost:3000/projects/{project_id}/analysis"}, indent=2))
    print(OUT.read_text())


if __name__ == "__main__":
    main()