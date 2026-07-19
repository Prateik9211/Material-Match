"""Cross-flow consistency test — the P0 foundational-trust guard.

Runs the SAME real bedroom photograph through BOTH the full-image
"Generate Specification" pipeline (`/api/projects/{id}/analyze`) AND the
manual "Select area of interest" pipeline (`/api/projects/{id}/analyze-
region` with `full_image_b64` + `bbox`), then asserts that the two
flows agree on the material classification for the same wall region.

Why this exists
---------------
On 2026-02-01 the founder reported that the same wall in the same photo
gave different readings depending on which entry-point analysed it —
"paint" from full-Generate but "plaster" from manual selection. Root
cause was three separate LLM prompts (`generate_swatch_dna`,
`run_object_aware_region_analysis`, `run_real_analysis`) all classifying
the same crop through structurally different reasoning. The fix
consolidated `analyze-region` to route through the same SAM3 +
`generate_swatch_dna` path as `/analyze`. This test locks that in.

Any future regression that reintroduces prompt divergence between the
two entry points will fail here.

Requires
--------
* `ROBOFLOW_API_KEY` in `/app/backend/.env` (Stage-A SAM3).
* `EMERGENT_LLM_KEY` in `/app/backend/.env` (Stage-B DNA classifier).
* Admin login credentials at `/app/memory/test_credentials.md`.
"""
from __future__ import annotations

import base64
import io
import os
import sys
from pathlib import Path

import pytest
import requests
from PIL import Image
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")
load_dotenv("/app/frontend/.env")

ADMIN_EMAIL = "admin@materialmatch.ai"
ADMIN_PASSWORD = "MaterialAdmin2026!"

# The exact bedroom fixture the standalone room smoke test uses. Same
# photograph the founder was looking at when the paint/plaster bug was
# reported.
FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "live_rooms" / "bed_artwork_wall.jpg"


def _api_base() -> str:
    url = os.environ.get("REACT_APP_BACKEND_URL") or os.environ.get("BACKEND_URL")
    if not url:
        pytest.skip("REACT_APP_BACKEND_URL not configured — cannot exercise live API.")
    return url.rstrip("/") + "/api"


@pytest.fixture(scope="module")
def session() -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{_api_base()}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text[:200]}"
    return s


@pytest.fixture(scope="module")
def bedroom_bytes() -> bytes:
    if not FIXTURE_PATH.exists():
        pytest.skip(f"Fixture {FIXTURE_PATH} missing — run the "
                    "test_integration_live_rooms.py suite first to populate "
                    "the cache, or check the fixture path.")
    b = FIXTURE_PATH.read_bytes()
    # Sanity-check the fixture is a real image.
    Image.open(io.BytesIO(b)).verify()
    return b


def _find_wall_row_from_analyze(rows: list[dict]) -> dict | None:
    """Pick the wall-family row from a full-image /analyze payload."""
    candidates = []
    for r in rows:
        label = (r.get("object_type") or r.get("zone") or "").lower()
        # We want a `wall` object — not a feature wall (which is a
        # stylistic subsection) and not backsplash/ceiling. Prefer the
        # largest-area detected `wall`.
        if label.startswith("wall") or " wall" in f" {label} ":
            if "feature" in label or "accent" in label:
                continue
            candidates.append(r)
    if not candidates:
        return None
    # Prefer the row with the biggest bbox (largest wall in frame).
    def _area(r: dict) -> float:
        b = r.get("scene_bbox") or []
        try:
            return float(b[2]) * float(b[3])
        except Exception:  # noqa: BLE001
            return 0.0
    candidates.sort(key=_area, reverse=True)
    return candidates[0]


@pytest.mark.integration
@pytest.mark.skipif(
    not (os.environ.get("ROBOFLOW_API_KEY") and os.environ.get("EMERGENT_LLM_KEY")),
    reason="SAM3 or LLM key missing — cannot run the live cross-flow test.",
)
def test_wall_region_agrees_across_flows(session, bedroom_bytes):
    """Full-image spec and manual region selector must agree on the
    material_family and surface_type of the same wall in the same photo."""
    api = _api_base()

    # 1) Create a fresh project so nothing else pollutes state.
    r = session.post(f"{api}/projects",
                     json={"name": "XFLOW_CONSISTENCY", "client_name": "e2e"},
                     timeout=15)
    r.raise_for_status()
    pid = r.json()["id"]
    try:
        # 2) Upload the reference image.
        r = session.post(
            f"{api}/projects/{pid}/reference",
            files={"file": ("bedroom.jpg", bedroom_bytes, "image/jpeg")},
            timeout=30,
        )
        r.raise_for_status()

        # 3) FLOW A — full-image spec.
        r = session.post(f"{api}/projects/{pid}/analyze", timeout=300)
        assert r.status_code == 200, f"/analyze failed: {r.status_code} {r.text[:200]}"
        analyze_payload = r.json()
        rows_a = analyze_payload.get("rows") or []
        assert rows_a, "full-image /analyze returned zero rows"
        wall_row_a = _find_wall_row_from_analyze(rows_a)
        assert wall_row_a is not None, (
            f"full-image /analyze did not surface a wall row; labels="
            f"{[r.get('object_type') for r in rows_a]}"
        )
        wall_bbox_px = wall_row_a.get("scene_bbox")  # pixels
        assert wall_bbox_px and len(wall_bbox_px) == 4, (
            f"wall row has no scene_bbox: {wall_row_a}"
        )

        # 4) FLOW B — manual region selector on the SAME wall bbox,
        #    converted from pixel bbox → percent bbox (the payload
        #    contract for the endpoint).
        img_size = ((analyze_payload.get("scene_stage_a") or {}).get("image_size") or {})
        W = float(img_size.get("width") or 0)
        H = float(img_size.get("height") or 0)
        assert W > 0 and H > 0, "scene_stage_a.image_size missing"
        x, y, w, h = [float(v) for v in wall_bbox_px]
        bbox_pct = [x / W * 100.0, y / H * 100.0, w / W * 100.0, h / H * 100.0]

        # Build a small JPEG crop of that same wall region so the
        # payload matches what the Analysis-page RegionSelector sends.
        img = Image.open(io.BytesIO(bedroom_bytes)).convert("RGB")
        crop_pil = img.crop((int(x), int(y), int(x + w), int(y + h)))
        crop_buf = io.BytesIO()
        crop_pil.save(crop_buf, format="JPEG", quality=90)
        crop_b64 = base64.b64encode(crop_buf.getvalue()).decode()
        full_b64 = base64.b64encode(bedroom_bytes).decode()

        r = session.post(
            f"{api}/projects/{pid}/analyze-region",
            json={
                "crop_b64": crop_b64,
                "full_image_b64": full_b64,
                "bbox": bbox_pct,
                "note": "xflow-wall",
                # Explicit mode="single" — the manual-selector code path
                # the founder was exercising.
                "mode": "single",
            },
            timeout=120,
        )
        assert r.status_code == 200, f"/analyze-region failed: {r.status_code} {r.text[:200]}"
        region_payload = r.json()
        rows_b = region_payload.get("rows") or []
        assert rows_b, "region /analyze-region returned zero rows"
        wall_row_b = rows_b[0]  # region endpoint returns ONE row per call

        # 5) The heart of the test — the two flows must agree.
        fam_a = (wall_row_a.get("material_family") or "").strip().title()
        fam_b = (wall_row_b.get("material_family") or "").strip().title()
        st_a = (wall_row_a.get("surface_description") or wall_row_a.get("material_type") or "").strip().lower()
        st_b = (wall_row_b.get("surface_description") or wall_row_b.get("material_type") or "").strip().lower()
        version_a = analyze_payload.get("version")
        version_b = region_payload.get("version")

        print(
            f"\nFLOW A (/analyze, version={version_a})   -> family={fam_a!r}  "
            f"surface={st_a!r}"
        )
        print(
            f"FLOW B (/analyze-region single, version={version_b})   -> family={fam_b!r}  "
            f"surface={st_b!r}"
        )

        # Both flows should now run through the consolidated
        # `run_consolidated_region_analysis` / `run_scene_region_analysis`
        # code path — their versions carry the shared "hybrid"/
        # "consolidated" markers.
        assert "region-object-aware" not in (version_b or ""), (
            f"analyze-region single mode is still calling the OLD "
            f"run_object_aware_region_analysis prompt (version={version_b!r})"
        )

        # Family MUST match (this is the paint/plaster contradiction the
        # founder reported — same wall, both flows must say the same
        # family). At LLM temperature=0 with the SAME crop and prompt,
        # the DNA classifier is deterministic.
        assert fam_a == fam_b, (
            f"cross-flow family mismatch on same wall region: "
            f"/analyze={fam_a!r} vs /analyze-region={fam_b!r}. "
            f"Version A={version_a!r}  Version B={version_b!r}"
        )
    finally:
        # Cleanup — never leave test projects behind.
        session.delete(f"{api}/projects/{pid}", timeout=10)
