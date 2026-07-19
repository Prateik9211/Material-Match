#!/usr/bin/env python3
"""End-to-end API smoke for the reported catalogue family leak.

Flow: fresh account -> create project -> upload real Unsplash interior ->
POST /api/projects/{id}/analyze -> inspect rows/pins/catalogue match families.
"""
import json
import time
from pathlib import Path

import requests

BASE = "http://localhost:8001/api"
IMAGE = Path("/app/test_reports/real_interior_test.jpg")
OUT = Path("/app/test_reports/api_real_image_iteration_20.json")


def main():
    s = requests.Session()
    email = f"qa-real-{int(time.time())}@materialmatch.ai"
    password = "Designer2026!"
    report = {"email": email, "steps": [], "violations": [], "warnings": []}

    def step(name, ok, data=None):
        report["steps"].append({"name": name, "ok": bool(ok), "data": data or {}})
        print(f"{'PASS' if ok else 'FAIL'}: {name} {data or ''}")

    r = s.post(f"{BASE}/auth/register", json={"email": email, "password": password, "name": "QA Real Image"}, timeout=30)
    step("fresh user registration", r.status_code == 200, {"status": r.status_code})
    r.raise_for_status()
    token = r.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}

    r = s.post(f"{BASE}/projects", json={"name": "QA real interior cross-family", "client_name": "QA"}, headers=headers, timeout=30)
    step("create project", r.status_code == 200, {"status": r.status_code})
    r.raise_for_status()
    project_id = r.json()["id"]
    report["project_id"] = project_id

    with IMAGE.open("rb") as f:
        r = s.post(f"{BASE}/projects/{project_id}/reference", files={"file": ("interior.jpg", f, "image/jpeg")}, headers=headers, timeout=60)
    step("upload real interior reference", r.status_code == 200, {"status": r.status_code, "response": r.json() if r.ok else r.text[:300]})
    r.raise_for_status()

    started = time.time()
    r = s.post(f"{BASE}/projects/{project_id}/analyze", headers=headers, timeout=240)
    elapsed = round(time.time() - started, 1)
    step("POST /projects/{id}/analyze returns without 500", r.status_code == 200, {"status": r.status_code, "elapsed_s": elapsed})
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

    rows_summary = []
    for i, row in enumerate(rows):
        matches = row.get("catalogue_matches") or []
        rows_summary.append({
            "i": i,
            "zone": row.get("zone"),
            "object_type": row.get("object_type"),
            "material_family": row.get("material_family"),
            "family_confidence": row.get("family_confidence"),
            "family_alternatives": row.get("family_alternatives"),
            "pin": row.get("pin"),
            "pin_source": row.get("pin_source"),
            "searched_categories": row.get("searched_categories"),
            "brain_object_locked": (row.get("brain") or {}).get("object_locked"),
            "match_families": [m.get("material_family") for m in matches],
            "match_categories": [m.get("category") for m in matches],
            "match_percents": [m.get("match_percent") for m in matches],
        })

        if not isinstance(row.get("pin"), dict):
            report["warnings"].append({"row": i, "issue": "row missing pin", "zone": row.get("zone")})
        if "catalogue_matches" not in row:
            report["violations"].append({"row": i, "issue": "catalogue_matches field missing", "zone": row.get("zone")})

        object_locked = bool((row.get("brain") or {}).get("object_locked"))
        obj = str(row.get("object_type") or "").lower()
        fam = str(row.get("material_family") or "").lower()
        # User-visible core: object-locked wall/ceiling Paint must never return laminate/veneer.
        if object_locked and obj in {"wall", "ceiling", "false_ceiling", "false ceiling"} and fam == "paint":
            bad = [m for m in matches if str(m.get("material_family") or "").lower() not in {"paint", "paints", "wall"}
                   or str(m.get("category") or "").lower() not in {"paint", "paints"}]
            if bad:
                report["violations"].append({
                    "row": i,
                    "issue": "object-locked Paint architectural row returned cross-family catalogue matches",
                    "zone": row.get("zone"),
                    "bad_matches": [{"name": m.get("material_name"), "family": m.get("material_family"), "category": m.get("category"), "percent": m.get("match_percent")} for m in bad],
                })

        # Flooring tile/stone should not be forced to laminate when it is classified as Tile/Stone.
        if object_locked and obj in {"floor", "flooring"} and fam in {"tile", "stone"}:
            bad = [m for m in matches if str(m.get("material_family") or "").lower() in {"laminate", "veneer"}
                   or str(m.get("category") or "").lower() in {"laminate", "laminates", "veneer", "veneers"}]
            if bad:
                report["violations"].append({
                    "row": i,
                    "issue": "object-locked Tile/Stone floor returned laminate/veneer catalogue matches",
                    "zone": row.get("zone"),
                    "bad_matches": [{"name": m.get("material_name"), "family": m.get("material_family"), "category": m.get("category"), "percent": m.get("match_percent")} for m in bad],
                })

    report["rows_summary"] = rows_summary
    object_locked_arch_paint_rows = [x for x in rows_summary if x.get("brain_object_locked") and str(x.get("object_type") or "").lower() in {"wall", "ceiling", "false_ceiling", "false ceiling"} and str(x.get("material_family") or "").lower() == "paint"]
    if not object_locked_arch_paint_rows:
        report["warnings"].append({"issue": "real image did not produce an object-locked wall/ceiling Paint row; direct backend tests cover this branch"})
    step("no cross-family catalogue matches on object-locked architectural Paint rows found in this run", not any(v["issue"].startswith("object-locked Paint") for v in report["violations"]), {"object_locked_arch_paint_rows": object_locked_arch_paint_rows})
    step("all returned rows include catalogue_matches field", not any(v.get("issue") == "catalogue_matches field missing" for v in report["violations"]), {})
    step("pins present for returned rows", len(report["warnings"]) == 0 or not any(w.get("issue") == "row missing pin" for w in report["warnings"]), {"pin_warnings": [w for w in report["warnings"] if w.get("issue") == "row missing pin"]})

    report["passed"] = len(report["violations"]) == 0 and len(rows) > 0
    OUT.write_text(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()