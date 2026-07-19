#!/usr/bin/env python3
"""Real-image API regression for founder-reported catalogue family leaks.

Fresh account -> project -> upload real interior -> analyze -> inspect returned
wall/ceiling/floor rows and catalogue match categories.
"""
import json
import time
from pathlib import Path

import requests

BASE = "http://localhost:8001/api"
IMAGE = Path("/app/test_reports/real_interior_test.jpg")
OUT = Path("/app/test_reports/api_real_image_iteration_21.json")


def main():
    sess = requests.Session()
    email = f"qa-real-iter21-{int(time.time())}@materialmatch.ai"
    password = "Designer2026!"
    report = {"email": email, "steps": [], "violations": [], "warnings": []}

    def step(name, ok, data=None):
        report["steps"].append({"name": name, "ok": bool(ok), "data": data or {}})
        print(f"{'PASS' if ok else 'FAIL'}: {name} {data or ''}")

    r = sess.post(f"{BASE}/auth/register", json={"email": email, "password": password, "name": "QA Real Iter21"}, timeout=30)
    step("fresh user registration", r.status_code == 200, {"status": r.status_code})
    r.raise_for_status()
    token = r.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}

    r = sess.post(f"{BASE}/projects", json={"name": "QA real interior iter21", "client_name": "QA"}, headers=headers, timeout=30)
    step("create project", r.status_code == 200, {"status": r.status_code})
    r.raise_for_status()
    project_id = r.json()["id"]
    report["project_id"] = project_id

    with IMAGE.open("rb") as f:
        r = sess.post(f"{BASE}/projects/{project_id}/reference", files={"file": ("interior.jpg", f, "image/jpeg")}, headers=headers, timeout=60)
    step("upload real interior reference", r.status_code == 200, {"status": r.status_code, "response": r.json() if r.ok else r.text[:300]})
    r.raise_for_status()

    started = time.time()
    r = sess.post(f"{BASE}/projects/{project_id}/analyze", headers=headers, timeout=300)
    elapsed = round(time.time() - started, 1)
    step("POST /projects/{id}/analyze returns 200", r.status_code == 200, {"status": r.status_code, "elapsed_s": elapsed, "body": None if r.ok else r.text[:1000]})
    if r.status_code != 200:
        report["analysis_error"] = r.text[:1000]
        OUT.write_text(json.dumps(report, indent=2))
        r.raise_for_status()

    analysis = r.json()
    rows = analysis.get("rows") or []
    report["analysis_meta"] = {
        "version": analysis.get("version"),
        "scene_fallback": analysis.get("scene_fallback"),
        "row_count": len(rows),
        "product_count": len(analysis.get("products") or []),
    }
    step("analysis has rows", len(rows) > 0, report["analysis_meta"])

    summaries = []
    wallish_rows = []
    floor_rows = []
    for i, row in enumerate(rows):
        matches = row.get("catalogue_matches") or []
        obj = str(row.get("object_type") or "").lower()
        group = str(row.get("group") or "").lower()
        zone = str(row.get("zone") or "").lower()
        fam = str(row.get("material_family") or "").lower()
        cats = [m.get("category") for m in matches]
        fams = [m.get("material_family") for m in matches]
        summary = {
            "i": i,
            "zone": row.get("zone"),
            "group": row.get("group"),
            "object_type": row.get("object_type"),
            "material_family": row.get("material_family"),
            "material_type": row.get("material_type"),
            "brain_allowed": (row.get("brain") or {}).get("allowed_categories"),
            "brain_object_locked": (row.get("brain") or {}).get("object_locked"),
            "searched_categories": row.get("searched_categories"),
            "match_categories": cats,
            "match_families": fams,
            "match_percents": [m.get("match_percent") for m in matches],
            "pin_source": row.get("pin_source"),
        }
        summaries.append(summary)

        is_wallish = obj in {"wall", "ceiling", "false_ceiling", "false ceiling"} or group in {"wall", "ceiling"} or "wall" in zone or "ceiling" in zone
        is_floor = obj in {"floor", "flooring"} or group == "floor" or "floor" in zone
        if is_wallish:
            wallish_rows.append(summary)
            bad = [m for m in matches if str(m.get("category") or "").lower() not in {"paint", "paints"}]
            if bad:
                report["violations"].append({
                    "row": i,
                    "issue": "wall/ceiling row returned non-Paint catalogue match",
                    "zone": row.get("zone"),
                    "family": row.get("material_family"),
                    "bad_matches": [{"name": m.get("material_name"), "family": m.get("material_family"), "category": m.get("category"), "percent": m.get("match_percent")} for m in bad],
                })
        if is_floor:
            floor_rows.append(summary)
            # The reported leak was floor routed to Laminates instead of tile/stone/wood.
            # Wood floors can legitimately route to Wood/Veneer/Laminates, but Tile/Stone
            # floor rows must never return Laminates/Veneers.
            if fam in {"tile", "stone"}:
                bad = [m for m in matches if str(m.get("category") or "").lower() in {"laminate", "laminates", "veneer", "veneers"}]
                if bad:
                    report["violations"].append({
                        "row": i,
                        "issue": "Tile/Stone flooring row returned Laminate/Veneer match",
                        "zone": row.get("zone"),
                        "family": row.get("material_family"),
                        "bad_matches": [{"name": m.get("material_name"), "family": m.get("material_family"), "category": m.get("category"), "percent": m.get("match_percent")} for m in bad],
                    })

    if not wallish_rows:
        report["warnings"].append({"issue": "real image did not produce wall/ceiling rows; direct backend tests cover wall branches"})
    if not floor_rows:
        report["warnings"].append({"issue": "real image did not produce floor rows"})
    report["rows_summary"] = summaries
    step("all wall/ceiling rows returned only Paint/Paints matches", not any(v["issue"] == "wall/ceiling row returned non-Paint catalogue match" for v in report["violations"]), {"wallish_rows": wallish_rows})
    step("Tile/Stone flooring rows did not return Laminate/Veneer matches", not any(v["issue"] == "Tile/Stone flooring row returned Laminate/Veneer match" for v in report["violations"]), {"floor_rows": floor_rows})

    report["passed"] = len(rows) > 0 and not report["violations"]
    OUT.write_text(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()