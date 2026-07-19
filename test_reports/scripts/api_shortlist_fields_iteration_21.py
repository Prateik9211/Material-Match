#!/usr/bin/env python3
"""API E2E for shortlist swatch field persistence."""
import json
import time
from pathlib import Path

import requests

BASE = "http://localhost:8001/api"
OUT = Path("/app/test_reports/api_shortlist_fields_iteration_21.json")


def main():
    sess = requests.Session()
    email = f"qa-shortlist-fields-{int(time.time())}@materialmatch.ai"
    password = "Designer2026!"
    report = {"email": email, "steps": [], "violations": []}

    def step(name, ok, data=None):
        report["steps"].append({"name": name, "ok": bool(ok), "data": data or {}})
        print(f"{'PASS' if ok else 'FAIL'}: {name} {data or ''}")

    r = sess.post(f"{BASE}/auth/register", json={"email": email, "password": password, "name": "QA Shortlist Fields"}, timeout=30)
    step("register fresh user", r.status_code == 200, {"status": r.status_code})
    r.raise_for_status()
    token = r.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}

    r = sess.post(f"{BASE}/projects", json={"name": "QA shortlist swatch fields", "client_name": "QA"}, headers=headers, timeout=30)
    step("create project", r.status_code == 200, {"status": r.status_code})
    r.raise_for_status()
    project_id = r.json()["id"]
    report["project_id"] = project_id

    payload = {
        "name": "QA Paints · Warm White 101",
        "source_type": "spec",
        "source": "QA Paints",
        "match_percent": 88,
        "category": "Paints",
        "zone": "Wall · QA region",
        "notes": "Code: WW-101",
        "image_ref": "",
        "external_url": "",
        "swatch_crop_b64": "dGVzdC1zd2F0Y2g=",
        "color_hex": "#F1EBE0",
        "material_code": "WW-101",
    }
    r = sess.post(f"{BASE}/projects/{project_id}/shortlist", json=payload, headers=headers, timeout=30)
    step("POST shortlist accepts and returns swatch fields", r.status_code == 200, {"status": r.status_code, "response": r.json() if r.ok else r.text[:300]})
    r.raise_for_status()
    created = r.json()
    for key in ("swatch_crop_b64", "color_hex", "material_code"):
        if created.get(key) != payload[key]:
            report["violations"].append({"step": "post", "field": key, "expected": payload[key], "actual": created.get(key)})

    r = sess.get(f"{BASE}/projects/{project_id}/shortlist", headers=headers, timeout=30)
    step("GET shortlist returns saved item", r.status_code == 200, {"status": r.status_code, "count": len((r.json().get("items") or []) if r.ok else [])})
    r.raise_for_status()
    items = r.json().get("items") or []
    found = next((it for it in items if it.get("id") == created.get("id")), None)
    if not found:
        report["violations"].append({"step": "get", "issue": "created item not found"})
    else:
        for key in ("swatch_crop_b64", "color_hex", "material_code"):
            if found.get(key) != payload[key]:
                report["violations"].append({"step": "get", "field": key, "expected": payload[key], "actual": found.get(key)})
    report["created"] = created
    report["get_found"] = found
    report["passed"] = not report["violations"]
    OUT.write_text(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()