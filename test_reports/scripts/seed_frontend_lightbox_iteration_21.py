#!/usr/bin/env python3
"""Seed a project for focused UI verification of hover and shortlist lightbox."""
import json
import os
import time
from pathlib import Path

import requests
from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient

BASE = "http://localhost:8001/api"
OUT = Path("/app/test_reports/frontend_lightbox_seed_iteration_21.json")


def main():
    sess = requests.Session()
    email = f"qa-lightbox-iter21-{int(time.time())}@materialmatch.ai"
    password = "Designer2026!"
    r = sess.post(f"{BASE}/auth/register", json={"email": email, "password": password, "name": "QA Lightbox Iter21"}, timeout=30)
    r.raise_for_status()
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = sess.post(f"{BASE}/projects", json={"name": "QA shortlist lightbox iter21", "client_name": "QA"}, headers=headers, timeout=30)
    r.raise_for_status()
    project_id = r.json()["id"]

    img = Path("/app/test_reports/real_interior_test.jpg")
    with img.open("rb") as f:
        r = sess.post(f"{BASE}/projects/{project_id}/reference", files={"file": ("ref.jpg", f, "image/jpeg")}, headers=headers, timeout=60)
    r.raise_for_status()

    load_dotenv("/app/backend/.env")
    client = MongoClient(os.environ["MONGO_URL"].strip('"'))
    db = client[os.environ["DB_NAME"].strip('"')]
    # A tiny valid 1x1 JPEG-like test payload is enough; browser only needs a src.
    swatch_b64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2w=="
    analysis = {
        "version": "qa-seeded-lightbox-iter21-v1",
        "generated_at": "2026-07-19T00:00:00+00:00",
        "summary": {"design_style": "QA", "material_palette": "Paint + Tile", "key_finishes": "Matte", "sourcing_note": ""},
        "rows": [
            {
                "zone": "Wall · QA region",
                "group": "Wall",
                "object_type": "wall",
                "material_family": "Paint",
                "material_type": "Matte wall paint",
                "color": "Warm white",
                "texture": "Smooth",
                "finish": "Matte",
                "classification": "Material Surface",
                "pin": {"x": 35, "y": 40},
                "pin_source": "scene_polygon_centroid",
                "searched_libraries": ["Paint Library"],
                "searched_categories": ["Paints"],
                "brain": {"classification": "Material Surface", "allowed_categories": ["Paints"], "allowed_libraries": ["Paint Library"], "excluded_libraries": ["Laminate Library"], "object_locked": True},
                "match_buckets": {"best": [{
                    "id": "qa-paint-1", "brand": "QA Paints", "catalogue": "QA shade card", "material_name": "Warm White 101",
                    "material_code": "WW-101", "material_family": "Paint", "category": "Paints", "finish": "Matte",
                    "color_name": "Warm white", "color_hex": "#F1EBE0", "swatch_crop_b64": swatch_b64,
                    "match_percent": 88, "source_library": "Seeded Library", "match_reason": "QA seeded match with swatch",
                    "similarity": {"visual": 88, "color": 96, "finish": 90, "texture": 80}
                }], "possible": [], "low": []},
                "catalogue_matches": [{
                    "id": "qa-paint-1", "brand": "QA Paints", "catalogue": "QA shade card", "material_name": "Warm White 101",
                    "material_code": "WW-101", "material_family": "Paint", "category": "Paints", "finish": "Matte",
                    "color_name": "Warm white", "color_hex": "#F1EBE0", "swatch_crop_b64": swatch_b64,
                    "match_percent": 88, "source_library": "Seeded Library", "match_reason": "QA seeded match with swatch",
                    "similarity": {"visual": 88, "color": 96, "finish": 90, "texture": 80}
                }]
            },
            {
                "zone": "Floor · QA region",
                "group": "Floor",
                "object_type": "floor",
                "material_family": "Tile",
                "material_type": "Light stone-look tile",
                "color": "Greige",
                "texture": "Fine stone texture",
                "finish": "Matte",
                "classification": "Material Surface",
                "pin": {"x": 55, "y": 82},
                "pin_source": "scene_bbox",
                "searched_libraries": ["Tile Library"],
                "searched_categories": ["Tiles", "Stone"],
                "brain": {"classification": "Material Surface", "allowed_categories": ["Tiles", "Stone"], "allowed_libraries": ["Tile Library", "Stone Library"], "excluded_libraries": ["Laminate Library"], "object_locked": True},
                "match_buckets": {"best": [], "possible": [], "low": []},
                "catalogue_matches": []
            }
        ]
    }
    db.projects.update_one({"_id": ObjectId(project_id)}, {"$set": {"mock_analysis": analysis, "status": "completed", "shortlist_items": []}})
    out = {"email": email, "password": password, "project_id": project_id, "analysis_url": f"http://localhost:3000/projects/{project_id}/analysis"}
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()