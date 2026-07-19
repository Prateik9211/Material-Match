#!/usr/bin/env python3
"""Focused live verification for the founder-reported feature/accent wall bug.

This script intentionally exercises the real backend /api/projects/{id}/analyze
flow with a real bedroom photo. It does not mock SAM3/LLM calls.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests


BASE_URL = "http://localhost:8001/api"
ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASSWORD = "MaterialAdmin2026!"
IMAGE_URL = "https://images.unsplash.com/photo-1616486338812-3dadae4b4ace?auto=format&fit=crop&w=1400&q=85"
IMAGE_PATH = Path("/tmp/feature_wall_bedroom_1616486338812.jpg")
SUMMARY_PATH = Path("/app/test_reports/feature_wall_live_iteration_25.json")
RESPONSE_PATH = Path("/app/test_reports/feature_wall_live_response_iteration_25.json")


def request_json(method: str, url: str, **kwargs) -> tuple[requests.Response, dict]:
    response = requests.request(method, url, **kwargs)
    try:
        payload = response.json()
    except Exception:
        payload = {"raw_text": response.text[:2000]}
    return response, payload


def download_image() -> None:
    if IMAGE_PATH.exists() and IMAGE_PATH.stat().st_size > 50_000:
        return
    r = requests.get(IMAGE_URL, timeout=60, headers={"User-Agent": "MaterialMatch-QA/1.0"})
    r.raise_for_status()
    if not r.content or len(r.content) < 50_000:
        raise AssertionError(f"Downloaded image too small: {len(r.content)} bytes")
    IMAGE_PATH.write_bytes(r.content)


def run_once() -> dict:
    started = time.time()
    summary: dict = {
        "image_url": IMAGE_URL,
        "steps": [],
        "feature_labels": ["feature wall", "accent wall"],
    }

    download_image()
    summary["image_path"] = str(IMAGE_PATH)
    summary["image_bytes"] = IMAGE_PATH.stat().st_size

    session = requests.Session()
    login_resp, login_payload = request_json(
        "POST",
        f"{BASE_URL}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    summary["steps"].append({"name": "login", "status_code": login_resp.status_code})
    login_resp.raise_for_status()
    token = login_payload.get("access_token")
    if not token:
        raise AssertionError(f"Login returned no access_token: {login_payload}")
    session.headers.update({"Authorization": f"Bearer {token}"})

    project_resp, project = request_json(
        "POST",
        f"{BASE_URL}/projects",
        json={"name": f"QA Feature Wall Live {int(time.time())}", "client_name": "QA", "notes": "feature wall live regression"},
        timeout=30,
        headers=session.headers,
    )
    summary["steps"].append({"name": "create_project", "status_code": project_resp.status_code})
    project_resp.raise_for_status()
    project_id = project.get("id")
    if not project_id:
        raise AssertionError(f"Create project returned no id: {project}")
    summary["project_id"] = project_id

    with IMAGE_PATH.open("rb") as f:
        upload_resp, upload_payload = request_json(
            "POST",
            f"{BASE_URL}/projects/{project_id}/reference",
            files={"file": (IMAGE_PATH.name, f, "image/jpeg")},
            timeout=60,
            headers=session.headers,
        )
    summary["steps"].append({"name": "upload_reference", "status_code": upload_resp.status_code, "payload": upload_payload})
    upload_resp.raise_for_status()

    analyze_resp, analysis = request_json(
        "POST",
        f"{BASE_URL}/projects/{project_id}/analyze",
        timeout=300,
        headers=session.headers,
    )
    elapsed = round(time.time() - started, 1)
    summary["steps"].append({"name": "analyze", "status_code": analyze_resp.status_code, "elapsed_seconds": elapsed})
    summary["analyze_status_code"] = analyze_resp.status_code
    summary["elapsed_seconds"] = elapsed
    RESPONSE_PATH.write_text(json.dumps(analysis, indent=2, default=str))
    analyze_resp.raise_for_status()

    rows = analysis.get("rows") or []
    objects = ((analysis.get("scene_stage_a") or {}).get("objects") or [])
    row_object_types = [(r.get("object_type") or r.get("zone") or "").strip().lower() for r in rows]
    stage_a_labels = [(o.get("label") or "").strip().lower() for o in objects]
    hit_labels = set(summary["feature_labels"])
    summary.update({
        "version": analysis.get("version"),
        "scene_fallback": analysis.get("scene_fallback"),
        "row_count": len(rows),
        "stage_a_object_count": len(objects),
        "row_object_types": row_object_types,
        "stage_a_labels": stage_a_labels,
        "rows_feature_or_accent_wall_present": any(label in hit_labels for label in row_object_types),
        "stage_a_feature_or_accent_wall_present": any(label in hit_labels for label in stage_a_labels),
        "feature_rows": [r for r in rows if (r.get("object_type") or "").strip().lower() in hit_labels],
        "feature_stage_a_objects": [o for o in objects if (o.get("label") or "").strip().lower() in hit_labels],
    })
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str))

    if analysis.get("scene_fallback"):
        raise AssertionError(f"Analyze fell back from live scene pipeline: {analysis.get('scene_fallback')}")
    if not summary["stage_a_feature_or_accent_wall_present"]:
        raise AssertionError(f"No feature/accent wall in scene_stage_a objects. Labels={stage_a_labels}")
    if not summary["rows_feature_or_accent_wall_present"]:
        raise AssertionError(f"No feature/accent wall row returned. Row object types={row_object_types}")
    return summary


def main() -> int:
    # If the first live analyze fails due to an upstream/transient issue, retry once
    # as requested. The second failure is preserved as the true test result.
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            result = run_once()
            result["attempt"] = attempt
            SUMMARY_PATH.write_text(json.dumps(result, indent=2, default=str))
            print(json.dumps(result, indent=2, default=str))
            return 0
        except Exception as exc:  # noqa: BLE001 - report exact live failure
            last_exc = exc
            partial = {}
            if SUMMARY_PATH.exists():
                try:
                    partial = json.loads(SUMMARY_PATH.read_text())
                except Exception:
                    partial = {}
            partial.setdefault("attempt_failures", []).append({"attempt": attempt, "error": repr(exc)})
            SUMMARY_PATH.write_text(json.dumps(partial, indent=2, default=str))
            print(f"ATTEMPT_{attempt}_FAILED: {exc!r}")
            # Retry only for server/upstream-looking failures, not for deterministic assertion failures.
            if attempt == 1 and ("HTTPError" in type(exc).__name__ or "Connection" in type(exc).__name__ or "Timeout" in type(exc).__name__):
                time.sleep(5)
                continue
            break
    raise SystemExit(f"TEST_FAILED: {last_exc!r}")


if __name__ == "__main__":
    main()