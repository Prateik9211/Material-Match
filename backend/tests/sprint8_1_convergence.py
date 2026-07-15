"""Sprint 8.1 — Engineering Convergence Harness (Image #2).

Same discipline as sprint8_convergence.py, but on ref_0 (a rendered bedroom
scene — very different lighting/composition/materials from site_0). Every
fix considered must improve the ENGINE generally, not overfit this image.
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

# Reuse the shared judge + IO helpers from sprint8_convergence.
sys.path.insert(0, str(Path(__file__).parent))
from sprint8_convergence import (   # noqa: E402
    _login, _create_project, judge_result, format_verdict,
)

BASE = os.environ.get("BASE_URL", "http://localhost:8001")
IMAGE_PATH = "/tmp/validation/ref_0_627a1ca5c4de7824.jpg"
OUT = Path("/tmp/validation/sprint8_1")
OUT.mkdir(exist_ok=True, parents=True)


# Image #2 zones — rendered bedroom scene, 1199x896 landscape.
ZONES = [
    {
        "id": "z1_headboard_wood_slat",
        "name": "Wood-slat headboard wall (warm oak)",
        "bbox_px": (400, 490, 400, 90),
        "expected_object": "wall",
        "expected_family": "Laminate",   # warm oak wood-clad wall
        "expected_hint": "warm medium oak wood-grain slat wall panel",
        "catalogue_has_match": True,
    },
    {
        "id": "z2_wall_paint",
        "name": "Wall paint above headboard (warm off-white)",
        "bbox_px": (250, 200, 180, 100),
        "expected_object": "wall",
        "expected_family": "Paint",
        "expected_hint": "warm off-white matte wall paint",
        "catalogue_has_match": True,
    },
    {
        "id": "z3_ceiling_paint",
        "name": "Ceiling — off-white matte paint",
        "bbox_px": (400, 30, 400, 100),
        "expected_object": "ceiling",
        "expected_family": "Paint",
        "expected_hint": "off-white matte ceiling paint",
        "catalogue_has_match": True,
    },
    {
        "id": "z4_floor_herringbone",
        "name": "Wooden floor — light oak herringbone",
        "bbox_px": (830, 850, 320, 40),
        "expected_object": "floor",
        "expected_family": "Laminate",
        "expected_hint": "light warm oak herringbone parquet floor",
        "catalogue_has_match": True,
    },
    {
        "id": "z5_bench_cushion",
        "name": "Bench cushion — cream / beige fabric (with visible bolsters)",
        "bbox_px": (380, 660, 450, 120),
        "expected_object": "sofa",
        "expected_family": "Fabric",
        "expected_hint": "cream beige plain upholstery fabric",
        "catalogue_has_match": False,   # catalogue has jute-weave fabrics only
    },
    {
        "id": "z6_bench_wood_arch",
        "name": "Bench wooden arch frame (warm oak)",
        "bbox_px": (450, 750, 320, 70),
        "expected_object": "bed",     # bench/bed-related
        "expected_family": "Laminate",
        "expected_hint": "warm oak wood grain arch bench frame",
        "catalogue_has_match": True,
    },
    {
        "id": "z7_sheer_curtain",
        "name": "Sheer curtain — white / cream fabric",
        "bbox_px": (1080, 300, 100, 350),
        "expected_object": "curtain",
        "expected_family": "Fabric",
        "expected_hint": "sheer white cream translucent curtain fabric",
        "catalogue_has_match": False,   # catalogue has no sheer curtains
    },
    {
        "id": "z8_nightstand_wood",
        "name": "Nightstand top — warm oak wood",
        "bbox_px": (860, 630, 100, 80),
        "expected_object": "tv unit",     # closest cabinetry match
        "expected_family": "Laminate",
        "expected_hint": "warm oak wood grain cabinetry surface",
        "catalogue_has_match": True,
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
        timeout=180,
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
        json={"name": "Sprint 8.1 Convergence · Image #2",
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
        print(f"    expect: object={zone['expected_object']}  family={zone['expected_family']}")
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
