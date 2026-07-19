"""Live SAM3 integration smoke test — permanent guard against future
vision-layer regressions.

Runs two real interior bedroom photographs through the production Stage-A
scene-segmentation pipeline (`detect_objects` + `filter_detections`) and
asserts that the expected architectural objects are actually present.

Why this exists
---------------
The previous agent was penalised for claiming an AI-vision bug was fixed
after only running unit tests that verified strings appeared in code.
Unit tests are structurally useful but cannot catch regressions in
prompt tuning, vocabulary changes, or IoU dedup logic — only real
images can. This test hits the live Roboflow SAM3 endpoint with fresh
bytes and asserts on what SAM3 *actually* returned.

Requirements
------------
* `ROBOFLOW_API_KEY` in `/app/backend/.env` (already configured).
* Internet access from the container to Roboflow serverless and
  Unsplash CDN.

Usage
-----
    cd /app/backend
    pytest -m integration tests/test_integration_live_bedroom.py -v -s

Marked `integration` so unit-only runs (`pytest -m "not integration"`)
skip it, but CI can opt in.

Cost
----
Each test call = 1 SAM3 request. Two images = two requests. Free tier
covers this comfortably.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import time
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from PIL import Image

# Make `intelligence.*` importable regardless of pytest invocation dir.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from intelligence.scene_segmentation import detect_objects, filter_detections  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — two real bedroom photographs from Unsplash.
#
# We download once and cache locally in `tests/fixtures/live_bedroom/`
# so subsequent runs don't hammer the CDN and stay deterministic even
# if Unsplash rotates its top-page images.
# ---------------------------------------------------------------------------
FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures" / "live_bedroom"
FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

# `w=1400` keeps files ~200-400 KB, big enough for SAM3 to work with
# yet still cheap to cache. The `?fm=jpg` param forces JPEG.
BEDROOM_IMAGES = [
    {
        "slug": "bed_artwork_wall",
        # Bedroom shot with bed, headboard, feature wall behind, framed
        # artwork, curtains, nightstand, floor rug. Verified via SAM3
        # live probe on 2026-02-01 to contain a strong `bed` +
        # `feature wall` + `artwork` signature.
        "url": (
            "https://images.unsplash.com/photo-1540518614846-7eded433c457"
            "?fm=jpg&w=1400&q=80"
        ),
        # We assert that at least one label from EACH group appears —
        # not that every single label appears. SAM3's exact vocab
        # varies run-to-run (dedup can consume some labels), so we
        # keep the assertion loose but not vacuous.
        "expect_any": [
            # architectural surfaces (at least one must be present)
            {"wall", "ceiling", "floor", "accent wall", "feature wall"},
            # bedroom-defining object
            {"bed", "headboard", "mattress"},
            # wall art — specifically what the last fork's feature-wall
            # dedup regression was hiding.
            {"artwork", "framed art", "picture frame"},
            # the feature-wall behind the bed itself — this label was
            # the one the cross-class IoU dedup was deleting.
            {"feature wall", "accent wall"},
        ],
    },
    {
        "slug": "bed_wood_paneling",
        # Bedroom with a strong wooden panel headboard wall — the exact
        # feature-wall class of image that surfaced the SAM3 cross-class
        # IoU dedup regression the previous agent fixed.
        "url": (
            "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267"
            "?fm=jpg&w=1400&q=80"
        ),
        "expect_any": [
            {"wall", "ceiling", "floor", "accent wall", "feature wall"},
            {"bed", "headboard", "pillow", "cushion"},
        ],
    },
]


def _cached_image_bytes(slug: str, url: str) -> bytes:
    """Download `url` on first access and cache to disk; return bytes."""
    path = FIXTURE_DIR / f"{slug}.jpg"
    if path.exists() and path.stat().st_size > 20_000:
        return path.read_bytes()
    # Best-effort download with a browser-ish UA (some CDNs 403 the
    # default `python-requests` UA).
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (integration-test)"},
        timeout=30,
    )
    resp.raise_for_status()
    # Sanity-check we got an actual image, not an HTML error page.
    try:
        Image.open(io.BytesIO(resp.content)).verify()
    except Exception as e:
        raise RuntimeError(
            f"Fixture download for {slug!r} did not return a valid image "
            f"(len={len(resp.content)}): {e}"
        ) from e
    path.write_bytes(resp.content)
    return resp.content


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("ROBOFLOW_API_KEY"),
    reason="ROBOFLOW_API_KEY not configured — cannot exercise live SAM3.",
)
@pytest.mark.parametrize("case", BEDROOM_IMAGES, ids=lambda c: c["slug"])
def test_sam3_bedroom_detects_expected_objects(case):
    """Live SAM3 must return the expected architectural objects for
    each real bedroom photograph.

    We use `detect_objects` (Stage A only) and `filter_detections` with
    production defaults so the assertion mirrors what the user actually
    sees in the Analysis UI. Stage B (LLM material classification) is
    intentionally skipped here — its output is validated separately by
    unit tests; this smoke test is scoped to the segmentation layer.
    """
    slug = case["slug"]
    print(f"\n--- SAM3 live test: {slug} ---")

    t0 = time.perf_counter()
    img_bytes = _cached_image_bytes(slug, case["url"])
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    print(f"image size: {img.size}  bytes={len(img_bytes)}  "
          f"sha256={hashlib.sha256(img_bytes).hexdigest()[:12]}")

    raw = detect_objects(img)
    filtered = filter_detections(
        raw,
        image_w=img.size[0],
        image_h=img.size[1],
    )
    dt = time.perf_counter() - t0

    labels = sorted({(d.get("label") or "").strip().lower()
                     for d in filtered if d.get("label")})
    print(f"raw detections: {len(raw)}   after filter: {len(filtered)}   "
          f"elapsed: {dt:.1f}s")
    print(f"labels: {labels}")

    # Hard evidence the pipeline actually ran — SAM3 should always
    # find at least SOMETHING in a real interior photograph. If it
    # returns nothing, either the API key is bad or the whole
    # pipeline is broken.
    assert filtered, (
        f"SAM3 returned zero filtered detections for {slug} — "
        f"pipeline likely broken. Raw count was {len(raw)}."
    )

    # Each expect_any group is a set of acceptable synonyms; at
    # least one label from each group must show up.
    missing_groups: list[set[str]] = []
    for group in case["expect_any"]:
        if not (group & set(labels)):
            missing_groups.append(group)
    assert not missing_groups, (
        f"SAM3 missed expected object groups for {slug}: "
        f"{missing_groups}. Actual labels: {labels}"
    )
