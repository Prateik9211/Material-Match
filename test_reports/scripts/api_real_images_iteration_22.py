#!/usr/bin/env python3
"""Focused real-image regression for cross-category catalogue leaks.

Creates a fresh user/project per image, uploads the reference, runs the real
analysis endpoint, and asserts wall/ceiling Paint routing and Tile/Stone floor
routing do not leak Laminate/Veneer matches.
"""
import json
import time
from pathlib import Path

import requests

BASE = "http://localhost:8001/api"
OUT = Path("/app/test_reports/api_real_images_iteration_22.json")
IMAGES = [
    ("bedroom", Path("/app/test_reports/real_interior_test.jpg")),
    ("kitchen", Path("/app/frontend/src/assets/landing/hero_kitchen_scene.jpg")),
]


def summarize_row(i, row):
    matches = row.get("catalogue_matches") or []
    return {
        "i": i,
        "zone": row.get("zone"),
        "group": row.get("group"),
        "object_type": row.get("object_type"),
        "material_family": row.get("material_family"),
        "material_type": row.get("material_type"),
        "brain_allowed": (row.get("brain") or {}).get("allowed_categories"),
        "brain_object_locked": (row.get("brain") or {}).get("object_locked"),
        "searched_categories": row.get("searched_categories"),
        "match_categories": [m.get("category") for m in matches],
        "match_families": [m.get("material_family") for m in matches],
        "match_percents": [m.get("match_percent") for m in matches],
        "pin_source": row.get("pin_source"),
    }


def analyze_image(label, image_path):
    sess = requests.Session()
    email = f"qa-real-{label}-iter22-{int(time.time())}@materialmatch.ai"
    password = "Designer2026!"
    result = {"label": label, "email": email, "image": str(image_path), "steps": [], "violations": [], "warnings": []}

    def step(name, ok, data=None):
        result["steps"].append({"name": name, "ok": bool(ok), "data": data or {}})
        print(f"{label}: {'PASS' if ok else 'FAIL'} {name} {data or ''}")

    r = sess.post(f"{BASE}/auth/register", json={"email": email, "password": password, "name": f"QA Real {label} Iter22"}, timeout=30)
    step("fresh user registration", r.status_code == 200, {"status": r.status_code})
    r.raise_for_status()
    headers = {"Authorization": f"Bearer {r.json().get('access_token')}"}

    r = sess.post(f"{BASE}/projects", json={"name": f"QA real {label} iter22", "client_name": "QA"}, headers=headers, timeout=30)
    step("create project", r.status_code == 200, {"status": r.status_code})
    r.raise_for_status()
    project_id = r.json()["id"]
    result["project_id"] = project_id

    with image_path.open("rb") as f:
        r = sess.post(f"{BASE}/projects/{project_id}/reference", files={"file": (image_path.name, f, "image/jpeg")}, headers=headers, timeout=60)
    step("upload real reference", r.status_code == 200, {"status": r.status_code, "response": r.json() if r.ok else r.text[:300]})
    r.raise_for_status()

    started = time.time()
    r = sess.post(f"{BASE}/projects/{project_id}/analyze", headers=headers, timeout=300)
    elapsed = round(time.time() - started, 1)
    step("POST analyze returns 200", r.status_code == 200, {"status": r.status_code, "elapsed_s": elapsed, "body": None if r.ok else r.text[:1000]})
    if r.status_code != 200:
        result["analysis_error"] = r.text[:1000]
        return result

    analysis = r.json()
    rows = analysis.get("rows") or []
    result["analysis_meta"] = {"version": analysis.get("version"), "scene_fallback": analysis.get("scene_fallback"), "row_count": len(rows), "product_count": len(analysis.get("products") or [])}
    step("analysis has rows", len(rows) > 0, result["analysis_meta"])

    summaries = []
    wallish = []
    floorish = []
    for i, row in enumerate(rows):
        summary = summarize_row(i, row)
        summaries.append(summary)
        obj = str(row.get("object_type") or "").lower()
        group = str(row.get("group") or "").lower()
        zone = str(row.get("zone") or "").lower()
        fam = str(row.get("material_family") or "").lower()
        matches = row.get("catalogue_matches") or []
        # Contract says wall/ceiling paint rows.  Do not fold application-
        # specific wall surfaces like backsplash into this assertion: the Brain
        # intentionally routes backsplash to waterproof tile/stone/laminate
        # libraries, while plain wall/ceiling objects must stay Paint-only.
        is_wallish = obj in {"wall", "ceiling", "false_ceiling", "false ceiling"} or group == "ceiling" or "ceiling" in zone
        is_floor = obj in {"floor", "flooring"} or group == "floor" or "floor" in zone
        if is_wallish:
            wallish.append(summary)
            bad = [m for m in matches if str(m.get("category") or "").lower() not in {"paint", "paints"}]
            if bad:
                result["violations"].append({"row": i, "issue": "wall/ceiling row returned non-Paint catalogue match", "zone": row.get("zone"), "family": row.get("material_family"), "bad_matches": [{"name": m.get("material_name"), "family": m.get("material_family"), "category": m.get("category"), "percent": m.get("match_percent")} for m in bad]})
        if is_floor:
            floorish.append(summary)
            if fam in {"tile", "stone"}:
                bad = [m for m in matches if str(m.get("category") or "").lower() in {"laminate", "laminates", "veneer", "veneers"}]
                if bad:
                    result["violations"].append({"row": i, "issue": "Tile/Stone flooring row returned Laminate/Veneer match", "zone": row.get("zone"), "family": row.get("material_family"), "bad_matches": [{"name": m.get("material_name"), "family": m.get("material_family"), "category": m.get("category"), "percent": m.get("match_percent")} for m in bad]})

    if not wallish:
        result["warnings"].append({"issue": "image did not produce wall/ceiling rows"})
    if not floorish:
        result["warnings"].append({"issue": "image did not produce floor rows"})
    result["rows_summary"] = summaries
    step("wall/ceiling rows returned only Paint/Paints matches", not any(v["issue"] == "wall/ceiling row returned non-Paint catalogue match" for v in result["violations"]), {"wallish_rows": wallish})
    step("Tile/Stone flooring rows did not return Laminate/Veneer matches", not any(v["issue"] == "Tile/Stone flooring row returned Laminate/Veneer match" for v in result["violations"]), {"floor_rows": floorish})
    result["passed"] = len(rows) > 0 and not result["violations"]
    return result


def main():
    report = {"images": [analyze_image(label, path) for label, path in IMAGES]}
    report["passed"] = all(img.get("passed") for img in report["images"])
    OUT.write_text(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()