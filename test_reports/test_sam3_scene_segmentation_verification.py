#!/usr/bin/env python3
"""Focused verification for SAM3 admin scene-segmentation parser fix.

Runs auth gating checks plus one live admin endpoint call. The live call uses
the requested kitchen validation image and should be kept to a single run to
avoid extra Roboflow credit usage.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests


BASE_URL = "http://localhost:8001/api"
IMAGE_PATH = Path("/tmp/validation/kitchen_3_sprint82.jpg")
ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASSWORD = "MaterialAdmin2026!"
OBJECT_LABELS = {
    "wall", "ceiling", "floor", "cabinet", "countertop",
    "backsplash", "sofa", "curtain", "plant",
}
MATERIAL_LABELS = {
    "painted wall", "wood paneling", "tile", "stone slab", "wallpaper",
    "laminate panel", "fabric upholstery", "metal fixture", "glass panel",
}


def post_image(token: str | None = None, timeout: int = 300) -> requests.Response:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with IMAGE_PATH.open("rb") as f:
        files = {"file": (IMAGE_PATH.name, f, "image/jpeg")}
        data = {"min_confidence": "0.55"}
        return requests.post(
            f"{BASE_URL}/admin/test-scene-segmentation",
            headers=headers,
            files=files,
            data=data,
            timeout=timeout,
        )


def login(email: str, password: str) -> str:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise AssertionError(f"login returned no access_token for {email}: {r.text[:200]}")
    return token


def register_non_admin() -> str:
    email = f"sam3_nonadmin_{int(time.time())}@example.com"
    r = requests.post(
        f"{BASE_URL}/auth/register",
        json={"email": email, "password": "Designer2026!", "name": "SAM3 Non Admin"},
        timeout=30,
    )
    if r.status_code == 400:
        return login(email, "Designer2026!")
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise AssertionError(f"register returned no access_token: {r.text[:200]}")
    return token


def valid_bbox(bbox) -> bool:
    return (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(v, (int, float)) for v in bbox)
        and all(v is not None for v in bbox)
    )


def validate_scene_response(payload: dict) -> list[str]:
    failures: list[str] = []
    objects = payload.get("objects") or []
    if payload.get("objects_kept_count", len(objects)) < 13 or len(objects) < 13:
        failures.append(
            f"expected at least 13 kept objects; got kept={payload.get('objects_kept_count')} len={len(objects)}"
        )
    for idx, obj in enumerate(objects):
        label = obj.get("label")
        if label not in OBJECT_LABELS or label == "?":
            failures.append(f"object {idx} has invalid label {label!r}")
        if not valid_bbox(obj.get("bbox")):
            failures.append(f"object {idx} label={label!r} has invalid bbox {obj.get('bbox')!r}")
        polygon = obj.get("polygon") or []
        if not isinstance(polygon, list) or len(polygon) == 0:
            failures.append(f"object {idx} label={label!r} has empty polygon")
        if obj.get("material_error") == "object has no bbox — skipped material pass":
            failures.append(f"object {idx} label={label!r} still skipped material pass because bbox missing")

    material_hits = []
    for oi, obj in enumerate(objects):
        crop_origin = obj.get("crop_origin")
        for mi, mat in enumerate(obj.get("materials") or []):
            material_hits.append((oi, mi, obj, mat))
            label = mat.get("label")
            conf = mat.get("confidence")
            if label not in MATERIAL_LABELS:
                failures.append(f"material {oi}.{mi} has invalid label {label!r}")
            if not isinstance(conf, (int, float)) or not (0 <= float(conf) <= 1):
                failures.append(f"material {oi}.{mi} label={label!r} has invalid confidence {conf!r}")
            # Endpoint contract names local bbox as `bbox`; review checklist also
            # referred to this as bbox_local, so validate local/global offset.
            if not valid_bbox(mat.get("bbox")):
                failures.append(f"material {oi}.{mi} label={label!r} has invalid local bbox {mat.get('bbox')!r}")
            if not valid_bbox(mat.get("bbox_global")):
                failures.append(f"material {oi}.{mi} label={label!r} has invalid bbox_global {mat.get('bbox_global')!r}")
            elif valid_bbox(mat.get("bbox")) and isinstance(crop_origin, list) and len(crop_origin) == 2:
                bx, by, bw, bh = mat["bbox"]
                gx, gy, gw, gh = mat["bbox_global"]
                if abs(gx - (bx + crop_origin[0])) > 1e-6 or abs(gy - (by + crop_origin[1])) > 1e-6 or abs(gw - bw) > 1e-6 or abs(gh - bh) > 1e-6:
                    failures.append(
                        f"material {oi}.{mi} bbox_global is not bbox offset by crop_origin"
                    )

    if not material_hits:
        failures.append("no material sub-detections returned on any object")

    backsplash = [o for o in objects if o.get("label") == "backsplash"]
    if not backsplash:
        failures.append("no backsplash object returned")
    else:
        mats = backsplash[0].get("materials") or []
        if not mats:
            failures.append("backsplash object returned no material sub-detections")
        else:
            for mi, mat in enumerate(mats):
                if mat.get("label") not in MATERIAL_LABELS:
                    failures.append(f"backsplash material {mi} invalid label {mat.get('label')!r}")
                if not isinstance(mat.get("confidence"), (int, float)):
                    failures.append(f"backsplash material {mi} invalid confidence {mat.get('confidence')!r}")
                if not (mat.get("polygon") and isinstance(mat.get("polygon"), list)):
                    failures.append(f"backsplash material {mi} missing real polygon")

    return failures


def main() -> int:
    result = {"steps": []}
    if not IMAGE_PATH.exists():
        raise FileNotFoundError(f"required validation image missing: {IMAGE_PATH}")

    unauth = post_image(timeout=60)
    result["steps"].append({"name": "unauth_post", "status_code": unauth.status_code, "body": unauth.text[:200]})
    if unauth.status_code != 401:
        raise AssertionError(f"Unauthed POST expected 401, got {unauth.status_code}: {unauth.text[:200]}")

    non_admin_token = register_non_admin()
    non_admin = post_image(non_admin_token, timeout=60)
    result["steps"].append({"name": "non_admin_post", "status_code": non_admin.status_code, "body": non_admin.text[:200]})
    if non_admin.status_code != 403:
        raise AssertionError(f"Non-admin POST expected 403, got {non_admin.status_code}: {non_admin.text[:200]}")

    admin_token = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    admin = post_image(admin_token, timeout=300)
    result["steps"].append({"name": "admin_post", "status_code": admin.status_code})
    result["admin_status_code"] = admin.status_code
    try:
        payload = admin.json()
    except Exception:
        payload = {"raw_text": admin.text[:1000]}
    Path("/tmp/sam3_live_verification_response.json").write_text(json.dumps(payload, indent=2))
    if admin.status_code != 200:
        raise AssertionError(f"Admin POST expected 200, got {admin.status_code}: {admin.text[:1000]}")

    failures = validate_scene_response(payload)
    result["objects_raw_count"] = payload.get("objects_raw_count")
    result["objects_kept_count"] = payload.get("objects_kept_count")
    result["labels"] = sorted({o.get("label") for o in payload.get("objects", [])})
    result["material_object_count"] = sum(1 for o in payload.get("objects", []) if o.get("materials"))
    result["backsplash_material_count"] = sum(
        len(o.get("materials") or []) for o in payload.get("objects", []) if o.get("label") == "backsplash"
    )
    result["failures"] = failures
    Path("/tmp/sam3_live_verification_summary.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if failures:
        raise AssertionError("; ".join(failures[:10]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"TEST_FAILED: {exc}", file=sys.stderr)
        raise