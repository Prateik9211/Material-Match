"""Sprint 8.2 — Engineering Convergence Harness (Image #3, fresh kitchen).

Third convergence sweep on a completely different domain from Images #1
and #2 (both bedrooms). Same discipline: run through the pipeline, judge
each region, apply only surgical GENERAL fixes to /app/backend/intelligence
until the engine either converges or honestly declines. Do NOT force
regions into CORRECT/COMPATIBLE — HONEST_REJECT and FAILURE are legitimate
outcomes.

Image identity
--------------
- Source     : Unsplash photo ID `1556909212-d5b604d0c90d`
- Downloaded : https://images.unsplash.com/photo-1556909212-d5b604d0c90d?w=1400&q=85
- Local path : /tmp/validation/kitchen_3_sprint82.jpg (1400 x 933, sha256
               a06d365f110cacf46526c4ba9b640d1c1ffb19cf975ac21bf13e4c753c1ee42e)
- Verified never previously used: `grep 1556909212 /app` returns no hits.

Scene: bright modern kitchen — white subway-tile backsplash, walnut floating
shelves, white shaker cabinetry, quartz island countertop, chrome gooseneck
tap, white shiplap ceiling, painted wall margin, red enamelware props.
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

sys.path.insert(0, str(Path(__file__).parent))
from sprint8_convergence import (  # noqa: E402
    _login, judge_result, format_verdict,
)

BASE = os.environ.get("BASE_URL", "http://localhost:8001")
IMAGE_PATH = "/tmp/validation/kitchen_3_sprint82.jpg"
OUT = Path("/tmp/validation/sprint8_2")
OUT.mkdir(exist_ok=True, parents=True)


# Image #3 zones — bright modern kitchen (1400 x 933).
#
# Bounding boxes are chosen from directly-visible, non-occluded areas of the
# reference photo. Each region is at least 60 px on its short side so the
# crop is a fair test (never chosen to inflate zone count).
#
# `catalogue_has_match`:
#   True  — catalogue clearly stocks the family + a colour-compatible SKU
#   False — catalogue has zero candidates in the correct family / colour
#   "maybe" — family present but colour/pattern coverage is thin;
#             HONEST_REJECT is a legitimate outcome
ZONES = [
    {
        "id": "z1_backsplash_subway_tile",
        "name": "Backsplash — white subway (glossy ceramic) tile above upper shelf",
        "bbox_px": (60, 145, 260, 60),
        "expected_object": "backsplash",
        "expected_family": "Tile",
        "expected_hint": "white glossy subway ceramic tile backsplash with grey grout",
        # Catalogue has Statuario/Ivory 300x600 & Calacatta Gold tiles — off-white
        # but no 3x6 subway. Family present; a compatible Ivory Matte 300x600 is
        # a legitimate designer sample. "maybe".
        "catalogue_has_match": "maybe",
    },
    {
        "id": "z2_floating_shelf_walnut",
        "name": "Floating shelf — warm walnut wood plank front face",
        "bbox_px": (35, 265, 285, 18),
        "expected_object": "built-in shelf",
        "expected_family": "Laminate",   # walnut-look laminate/veneer
        "expected_hint": "warm walnut wood grain floating shelf edge",
        # 13+ walnut/dark-oak veneers & laminates in seed.
        "catalogue_has_match": True,
    },
    {
        "id": "z3_lower_cabinet_paint",
        "name": "Lower cabinetry — white painted shaker drawer front",
        "bbox_px": (60, 640, 180, 90),
        "expected_object": "kitchen cabinet",
        "expected_family": "Paint",
        "expected_hint": "white matte painted mdf shaker kitchen cabinet drawer front",
        # 20+ white/off-white paints in seed.
        "catalogue_has_match": True,
    },
    {
        "id": "z4_countertop_white_quartz",
        "name": "Countertop — white quartz / marble-look stone slab (island foreground)",
        "bbox_px": (30, 820, 500, 55),
        "expected_object": "countertop",
        "expected_family": "Stone",
        "expected_hint": "white quartz stone countertop with subtle vein",
        # Statuario White Quartz, Frosty Carrina, Makrana Cream, Statuario 1600x3200 tile.
        "catalogue_has_match": True,
    },
    {
        "id": "z5_ceiling_shiplap_paint",
        "name": "Ceiling — white painted shiplap / beadboard (top strip)",
        "bbox_px": (400, 5, 600, 28),
        "expected_object": "ceiling",
        "expected_family": "Paint",
        "expected_hint": "white matte painted shiplap ceiling",
        # Whites in Paints. "True".
        "catalogue_has_match": True,
    },
    {
        "id": "z6_wall_paint_margin",
        "name": "Wall — off-white matte paint (top-right margin, above the door)",
        "bbox_px": (1080, 155, 200, 80),
        "expected_object": "wall",
        "expected_family": "Paint",
        "expected_hint": "warm off-white matte wall paint",
        "catalogue_has_match": True,
    },
    {
        "id": "z7_tap_chrome_gooseneck",
        "name": "Kitchen tap — chrome / stainless gooseneck faucet",
        "bbox_px": (555, 460, 105, 145),
        "expected_object": "wall",   # nothing in catalog treats "faucet" as an object
        "expected_family": "Metal",
        "expected_hint": "chrome stainless gooseneck kitchen faucet tap fixture",
        # Catalogue Hardware is mostly brass/rose-gold + one nickel hinge; no
        # chrome tap SKU. Family present but colour/finish don't match.
        # HONEST_REJECT is the correct outcome.
        "catalogue_has_match": False,
    },
]


def _crop_pack(zone: dict) -> tuple[str, str, list]:
    img = Image.open(IMAGE_PATH).convert("RGB")
    W, H = img.size
    x, y, w, h = zone["bbox_px"]
    x2, y2 = min(W, x + w), min(H, y + h)
    crop = img.crop((x, y, x2, y2))
    crop.save(OUT / f"crop_{zone['id']}.jpg", "JPEG", quality=90)

    full_buf = io.BytesIO(); img.save(full_buf, "JPEG", quality=85)
    crop_buf = io.BytesIO(); crop.save(crop_buf, "JPEG", quality=90)
    return (
        base64.b64encode(full_buf.getvalue()).decode(),
        base64.b64encode(crop_buf.getvalue()).decode(),
        [x / W * 100, y / H * 100, w / W * 100, h / H * 100],
    )


def _call_analyze(token: str, pid: str, zone: dict) -> tuple[dict, float]:
    full_b64, crop_b64, bbox_pct = _crop_pack(zone)
    t0 = time.time()
    r = requests.post(
        f"{BASE}/api/projects/{pid}/analyze-region",
        json={"crop_b64": crop_b64, "full_image_b64": full_b64,
              "bbox": bbox_pct, "note": zone["expected_hint"]},
        headers={"Authorization": f"Bearer {token}"},
        timeout=240,
    )
    elapsed = time.time() - t0
    if r.status_code != 200:
        return {"__http__": r.status_code, "body": r.text[:400]}, elapsed
    return r.json(), elapsed


def run_all() -> list[dict]:
    print(">> Logging in / creating project ...")
    token = _login()
    r = requests.post(
        f"{BASE}/api/projects",
        json={"name": "Sprint 8.2 Convergence · Image #3 (kitchen)",
              "client_name": "Convergence Harness"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    r.raise_for_status()
    pid = r.json()["id"]
    print(f"   project_id={pid}\n")

    results = []
    for i, zone in enumerate(ZONES, 1):
        print(f"{'=' * 72}\n[{i}/{len(ZONES)}] {zone['id']}  ·  {zone['name']}")
        print(f"    expect: object={zone['expected_object']}  "
              f"family={zone['expected_family']}  "
              f"catalogue_has_match={zone['catalogue_has_match']}")
        try:
            resp, elapsed = _call_analyze(token, pid, zone)
        except Exception as e:
            print(f"    !! transport error: {e}")
            results.append({"zone": zone, "verdict": "FAILURE",
                            "root_cause": "implementation bug",
                            "error": str(e), "evidence": {}, "top3": [],
                            "row": {}})
            continue
        verdict, cause, ev = judge_result(zone, resp)
        row = (resp.get("rows") or [{}])[0]
        top3 = row.get("catalogue_matches") or []
        results.append({
            "zone": zone, "verdict": verdict, "root_cause": cause,
            "evidence": ev, "elapsed_s": round(elapsed, 1),
            "top3": [
                {
                    "brand": m.get("brand"), "name": m.get("material_name"),
                    "code": m.get("material_code"),
                    "family": m.get("category") or m.get("material_family"),
                    "match_pct": m.get("match_percent"),
                    "visually_verified": m.get("visually_verified", False),
                    "reason": (m.get("match_reason") or "")[:200],
                }
                for m in top3[:3]
            ],
            "ai_description": (row.get("match_state") or {}).get("ai_description"),
            "row": {
                "material_family": row.get("material_family"),
                "material_type": row.get("material_type"),
                "color": row.get("color"),
                "finish": row.get("finish"),
                "confidence": row.get("confidence"),
                "match_state": row.get("match_state"),
                "rerank": row.get("rerank"),
                "brain": row.get("brain"),
                "zone": row.get("zone"),
            },
        })
        print(f"    got: object={ev.get('detected_object')}  "
              f"cls_fam={ev.get('classifier_family')}  "
              f"vis_fam={ev.get('vision_family')}  "
              f"final={ev.get('final_family')}  "
              f"override={ev.get('family_override')}")
        for k, m in enumerate(top3[:3]):
            mark = "OK " if m.get("visually_verified") else "..."
            print(f"      [{k+1}] {mark} {m.get('match_percent'):>3}%  "
                  f"{m.get('brand')} / {m.get('material_name')} "
                  f"({m.get('material_code')}) [{m.get('category')}]")
        print(f"    elapsed {elapsed:.1f}s  →  {format_verdict(verdict)}"
              + (f"  · {cause}" if cause else ""))

    print("\n" + "=" * 72)
    verdicts = {}
    for r in results:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    for v, n in verdicts.items():
        print(f"  {format_verdict(v):<20} {n}")

    Path(OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\n→ results saved: {OUT / 'results.json'}")
    return results


if __name__ == "__main__":
    run_all()
