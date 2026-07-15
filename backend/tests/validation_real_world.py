"""Real-world Sprint 7 validation harness.

Runs the live `analyze-region` endpoint against real interior photos
already in the database and reports match quality per zone. Success gate:
correct product must appear in Top-3 with confidence >= 70.

Usage:
  python3 /app/backend/tests/validation_real_world.py
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import requests
from PIL import Image

BASE = os.environ.get("BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASSWORD = "MaterialAdmin2026!"

OUT = Path("/tmp/validation")
OUT.mkdir(exist_ok=True, parents=True)


# ---- Zone definitions -----------------------------------------------------
# Each zone: (name, source_image_path, bbox_pixels(x,y,w,h), expected_family,
#             expected_hint) — bbox is in ORIGINAL pixel coords.
# Site image: 900x1600 (portrait). It shows a real Indian bedroom.
# Layout notes verified by manually inspecting the JPEG:
#   * Dark walnut wardrobe columns on left (~0..300, 350..1200)
#   * Cream/beige cane textured panels inset in wardrobe doors
#     (~55..220, 450..600)
#   * Cream fabric headboard behind bed (~330..600, 850..1050)
#   * Light oak wooden floor (~200..800, 1400..1600)
#   * White ceiling with wooden false-ceiling frame (~200..750, 0..300)

SITE = "/tmp/validation/site_0_860403ee827fc0d8.jpg"
REF0 = "/tmp/validation/ref_0_627a1ca5c4de7824.jpg"  # design render (bedroom)

ZONES = [
    {
        "name": "Dark walnut wardrobe panel",
        "img": SITE,
        "bbox_px": (720, 850, 180, 400),
        "expected_family": "Laminate",  # dark walnut wood-grain laminate
        "expected_hint": "dark brown walnut wood grain",
    },
    {
        "name": "Light oak floor",
        "img": SITE,
        "bbox_px": (250, 1450, 500, 130),
        "expected_family": "Laminate",  # or Wood — light oak plank floor
        "expected_hint": "light warm oak wood plank",
    },
    {
        "name": "Cream fabric headboard",
        "img": SITE,
        "bbox_px": (340, 870, 240, 130),
        "expected_family": "Fabric",
        "expected_hint": "cream beige plain fabric upholstery",
    },
    {
        "name": "White wall and ceiling",
        "img": SITE,
        "bbox_px": (300, 100, 380, 150),
        "expected_family": "Paint",
        "expected_hint": "off-white matte wall paint",
    },
    {
        "name": "Wood-tone slat headboard (render)",
        "img": REF0,
        "bbox_px": (330, 480, 500, 100),
        "expected_family": "Laminate",  # warm oak slat wall
        "expected_hint": "warm medium oak wood grain",
    },
]


def _login() -> str:
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=20)
    r.raise_for_status()
    j = r.json()
    return j.get("token") or j.get("access_token")


def _create_project(token: str, name: str) -> str:
    r = requests.post(
        f"{BASE}/api/projects",
        json={"name": name, "client_name": "Real World Validation"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["id"]


def _crop_to_b64(img_path: str, bbox_px: tuple, out_name: str) -> tuple[str, str, list]:
    """Return (full_b64, crop_b64, bbox_percent)."""
    img = Image.open(img_path).convert("RGB")
    W, H = img.size
    x, y, w, h = bbox_px
    x2, y2 = min(W, x + w), min(H, y + h)
    crop = img.crop((x, y, x2, y2))
    crop.save(OUT / f"crop_{out_name}.jpg", "JPEG", quality=90)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    full_b64 = base64.b64encode(buf.getvalue()).decode()

    buf2 = io.BytesIO()
    crop.save(buf2, "JPEG", quality=90)
    crop_b64 = base64.b64encode(buf2.getvalue()).decode()

    bbox_pct = [
        round(x / W * 100, 2), round(y / H * 100, 2),
        round(w / W * 100, 2), round(h / H * 100, 2),
    ]
    return full_b64, crop_b64, bbox_pct


def _analyze(token: str, project_id: str, full_b64: str,
             crop_b64: str, bbox_pct: list, note: str) -> dict:
    r = requests.post(
        f"{BASE}/api/projects/{project_id}/analyze-region",
        json={
            "crop_b64": crop_b64,
            "full_image_b64": full_b64,
            "bbox": bbox_pct,
            "note": note,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=180,
    )
    if r.status_code != 200:
        return {"error": r.status_code, "body": r.text[:400]}
    return r.json()


def _summarise_row(row: dict) -> dict:
    matches = row.get("catalogue_matches") or []
    top3 = []
    for m in matches[:3]:
        top3.append({
            "brand": m.get("brand"),
            "name": m.get("material_name"),
            "code": m.get("material_code"),
            "family": m.get("category") or m.get("material_family"),
            "match_pct": m.get("match_percent"),
            "visually_verified": m.get("visually_verified", False),
            "reason": (m.get("match_reason") or "")[:160],
        })
    rerank = row.get("rerank") or {}
    match_state = row.get("match_state") or {}
    return {
        "object_type": row.get("object_type"),
        "family_routing": row.get("family_routing") or {},
        "ai_family": row.get("material_family"),
        "ai_material_type": row.get("material_type"),
        "ai_color": row.get("color"),
        "ai_finish": row.get("finish"),
        "confidence": row.get("confidence"),
        "brain_allowed_categories": (row.get("brain") or {}).get("allowed_categories"),
        "no_confident_match": match_state.get("no_confident_match", False),
        "ai_description": match_state.get("ai_description"),
        "rerank": rerank,
        "top3": top3,
    }


def main() -> None:
    print(">> Logging in...")
    token = _login()
    print(">> Creating project...")
    pid = _create_project(token, "Sprint 7 Real World Validation")
    print(f"   project_id={pid}")

    results = []
    for i, zone in enumerate(ZONES, 1):
        print(f"\n{'=' * 60}\n[{i}/{len(ZONES)}] {zone['name']}")
        print(f"    expected family = {zone['expected_family']}")
        print(f"    expected hint   = {zone['expected_hint']}")
        try:
            full_b64, crop_b64, bbox_pct = _crop_to_b64(
                zone["img"], zone["bbox_px"], f"{i}_{zone['name'].replace(' ', '_')}"
            )
        except Exception as e:
            print(f"    !! crop failed: {e}")
            continue
        t0 = time.time()
        try:
            resp = _analyze(token, pid, full_b64, crop_b64, bbox_pct, zone["expected_hint"])
        except Exception as e:
            print(f"    !! analyze error: {e}")
            results.append({"zone": zone["name"], "error": str(e)})
            continue
        elapsed = time.time() - t0
        if "error" in resp:
            print(f"    !! HTTP {resp['error']}: {resp['body']}")
            results.append({"zone": zone["name"], **resp})
            continue
        rows = resp.get("rows") or []
        if not rows:
            print("    !! no rows returned")
            results.append({"zone": zone["name"], "empty": True, "raw": resp})
            continue
        # analyze-region can return multiple rows; keep them all
        zone_result = {
            "zone": zone["name"],
            "expected_family": zone["expected_family"],
            "expected_hint": zone["expected_hint"],
            "elapsed_s": round(elapsed, 1),
            "row_count": len(rows),
            "rows": [_summarise_row(r) for r in rows],
        }
        results.append(zone_result)
        for j, r in enumerate(zone_result["rows"]):
            fr = r.get("family_routing") or {}
            print(f"    Row {j+1}: object={r['object_type']} "
                  f"| classifier_family={fr.get('classifier_family')} "
                  f"vision_family={fr.get('vision_family')} "
                  f"final_family={fr.get('final_family')} "
                  f"override={fr.get('override_applied')}")
            print(f"      routing_reason: {fr.get('reason')}")
            print(f"      brain_allowed: {r.get('brain_allowed_categories')}")
            print(f"      ai_type='{r['ai_material_type']}' color='{r['ai_color']}' "
                  f"conf={r['confidence']}%")
            if r["no_confident_match"]:
                print(f"      HONEST REJECT: {r['ai_description']}")
                continue
            for k, m in enumerate(r["top3"]):
                mark = "OK " if m["visually_verified"] else "..."
                print(f"      [{k+1}] {mark} {m['match_pct']:>3}%  "
                      f"{m['brand']} / {m['name']} ({m['code']}) [{m['family']}]")
                if m["reason"]:
                    print(f"           reason: {m['reason']}")
            if r.get("rerank"):
                print(f"      rerank: {r['rerank']}")
        print(f"    elapsed: {elapsed:.1f}s")

    out_path = OUT / "results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved: {out_path}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print("HTTP error:", e, "body:", getattr(e.response, "text", "")[:400])
        sys.exit(1)
