"""SAM3 (Roboflow-hosted) scene segmentation — validation-only helper.

Two-pass strategy the product owner wants to validate on 30-40 test images:

  Pass 1 — `detect_objects(image)`
      Send the whole room photo with a fixed architectural vocabulary
      ("wall", "cabinet", "countertop", ...). SAM3 returns one segmentation
      per prompt with polygon masks and confidence scores.

  Pass 2 — `detect_materials_in_crop(image, bbox, material_vocab)`
      Crop the original image to the object's bbox, resend to SAM3 with
      material-concept prompts ("painted wall", "wood paneling", "tile").
      Returns detections in the CROP's local coordinates plus the crop
      origin so callers can map back to the original image.

Post-processing — `filter_detections(detections, min_confidence)`
      Drops sub-threshold detections and dedups masks with heavy overlap.

Deliberately self-contained: no imports from `dna.py`, `retrieval.py`,
`rerank.py`, `embeddings.py`, or the catalogue matching code. This is a
validation harness, not part of the live user pipeline.

Requires env var `ROBOFLOW_API_KEY`. Fails fast with a clear error when
missing so callers can surface an admin-friendly message.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from typing import Any, Iterable

import requests
from PIL import Image

logger = logging.getLogger(__name__)

SAM3_ENDPOINT = "https://serverless.roboflow.com/sam3/concept_segment"
SAM3_TIMEOUT_S = int(os.environ.get("SAM3_TIMEOUT_S", "60"))
SAM3_MAX_LONGEST_EDGE = 1024   # Roboflow SAM3 hosted cap (1024x1024).
SAM3_MAX_BYTES = 2 * 1024 * 1024  # Roboflow SAM3 hosted cap (2 MB).
SAM3_MAX_PROMPTS = 16          # Roboflow SAM3 hosted cap.

# Fixed architectural vocabulary for pass 1. Small on purpose — easy to
# extend as we learn what the model responds well to on real interiors.
ARCHITECTURAL_VOCAB: tuple[str, ...] = (
    "wall", "ceiling", "floor", "cabinet", "countertop",
    "backsplash", "sofa", "curtain", "plant",
)


class Sam3Error(RuntimeError):
    """Any failure originating from the SAM3 call — missing key, bad
    response, network error, or the API returning a non-2xx status."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _get_api_key() -> str:
    key = os.environ.get("ROBOFLOW_API_KEY", "").strip()
    if not key:
        raise Sam3Error(
            "ROBOFLOW_API_KEY environment variable is not set. Add it via "
            "Emergent's secrets configuration panel and restart the backend."
        )
    return key


def _to_pil(image: Any) -> Image.Image:
    """Accept bytes, base64, Image.Image, or a filesystem path."""
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(bytes(image))).convert("RGB")
    if isinstance(image, str):
        # base64 (with or without data-URL prefix) OR filesystem path.
        if image.startswith("data:"):
            image = image.split(",", 1)[-1]
        if len(image) > 400 and not os.path.isfile(image):
            try:
                return Image.open(io.BytesIO(base64.b64decode(image))).convert("RGB")
            except Exception as e:
                raise Sam3Error(f"Could not decode image as base64: {e}") from e
        return Image.open(image).convert("RGB")
    raise Sam3Error(f"Unsupported image type: {type(image).__name__}")


def _prepare_payload_image(img: Image.Image) -> tuple[str, tuple[int, int], float]:
    """Downscale to Roboflow's 1024/2MB limits, return (base64, (W,H), scale).

    `scale` is (sent_edge / original_edge) — the caller multiplies detection
    coordinates by 1/scale to map back to the original image.
    """
    w, h = img.size
    longest = max(w, h)
    scale = 1.0
    if longest > SAM3_MAX_LONGEST_EDGE:
        scale = SAM3_MAX_LONGEST_EDGE / longest
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    quality = 88
    while True:
        buf.seek(0); buf.truncate()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() <= SAM3_MAX_BYTES or quality <= 55:
            break
        quality -= 8
    return (
        base64.b64encode(buf.getvalue()).decode("ascii"),
        img.size,
        scale,
    )


def _post_sam3(image_b64: str, prompts: list[str]) -> dict:
    if not prompts:
        raise Sam3Error("`prompts` must be a non-empty list of strings.")
    if len(prompts) > SAM3_MAX_PROMPTS:
        raise Sam3Error(
            f"SAM3 accepts at most {SAM3_MAX_PROMPTS} prompts per request "
            f"(got {len(prompts)})."
        )
    api_key = _get_api_key()
    payload = {
        "format": "polygon",
        "image": {"type": "base64", "value": image_b64},
        "prompts": [{"type": "text", "text": p} for p in prompts],
    }
    try:
        r = requests.post(
            SAM3_ENDPOINT,
            params={"api_key": api_key},
            json=payload,
            timeout=SAM3_TIMEOUT_S,
            headers={"Content-Type": "application/json"},
        )
    except requests.RequestException as e:
        raise Sam3Error(f"SAM3 network error: {e}") from e
    if r.status_code == 401 or r.status_code == 403:
        raise Sam3Error(
            f"SAM3 rejected the API key (HTTP {r.status_code}). "
            f"Verify ROBOFLOW_API_KEY is valid: {r.text[:200]}"
        )
    if r.status_code != 200:
        raise Sam3Error(f"SAM3 HTTP {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except ValueError as e:
        raise Sam3Error(f"SAM3 returned non-JSON body: {e}") from e


def _polygon_bbox(polygon: list[dict[str, float]]) -> tuple[float, float, float, float] | None:
    """Return (x, y, w, h) axis-aligned bbox from a polygon of {x,y} points."""
    if not polygon:
        return None
    xs = [float(p.get("x", 0)) for p in polygon]
    ys = [float(p.get("y", 0)) for p in polygon]
    if not xs or not ys:
        return None
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    return (x_min, y_min, x_max - x_min, y_max - y_min)


def _parse_prompt_results(payload: dict, scale_back: float) -> list[dict]:
    """Flatten Roboflow's prompt_results into a list of detections.

    `scale_back` (>= 1.0) multiplies polygon and bbox coordinates so they map
    to the caller's ORIGINAL (pre-downscale) image space. When the image
    wasn't downscaled, scale_back == 1.0 and coordinates pass through.
    """
    detections: list[dict] = []
    prompt_results = payload.get("prompt_results") or payload.get("predictions") or []
    for pr in prompt_results:
        label = pr.get("prompt") or pr.get("class") or pr.get("text") or "?"
        if isinstance(label, dict):
            label = label.get("text", "?")
        # Each prompt may return multiple detections (e.g. two cabinets).
        preds = (
            pr.get("predictions")
            or pr.get("detections")
            or pr.get("masks")
            or ([pr] if pr.get("polygon") or pr.get("points") else [])
        )
        for p in preds:
            conf = float(p.get("confidence", p.get("score", 0.0)) or 0.0)
            poly = p.get("polygon") or p.get("points") or []
            # Roboflow sometimes wraps polygons in a list of contours.
            if poly and isinstance(poly[0], list):
                # Multi-contour — pick the largest by point count for the
                # single-mask representation; keep all contours in `polygons`.
                all_contours = [
                    [_scale_point(pt, scale_back) for pt in contour]
                    for contour in poly
                ]
                primary = max(all_contours, key=len)
            else:
                primary = [_scale_point(pt, scale_back) for pt in poly]
                all_contours = [primary] if primary else []
            bbox = _polygon_bbox(primary) if primary else None
            # Fall back to Roboflow's own bbox when polygon missing.
            if bbox is None and p.get("x") is not None:
                bbox = (
                    float(p["x"]) - float(p.get("width", 0)) / 2,
                    float(p["y"]) - float(p.get("height", 0)) / 2,
                    float(p.get("width", 0)),
                    float(p.get("height", 0)),
                )
                bbox = tuple(v * scale_back for v in bbox)
            detections.append({
                "label": str(label),
                "confidence": conf,
                "bbox": list(bbox) if bbox else None,
                "polygon": primary,
                "polygons": all_contours,
            })
    return detections


def _scale_point(pt: Any, scale_back: float) -> dict[str, float]:
    if isinstance(pt, dict):
        return {"x": float(pt.get("x", 0)) * scale_back,
                "y": float(pt.get("y", 0)) * scale_back}
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        return {"x": float(pt[0]) * scale_back,
                "y": float(pt[1]) * scale_back}
    return {"x": 0.0, "y": 0.0}


def _bbox_iou(a: list[float] | tuple, b: list[float] | tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def detect_objects(image: Any, vocab: Iterable[str] | None = None) -> list[dict]:
    """Pass 1 — architectural-object detection.

    Args:
        image: PIL Image, raw bytes, base64 string, or filesystem path.
        vocab: Optional prompt vocabulary; defaults to `ARCHITECTURAL_VOCAB`.

    Returns:
        List of {label, bbox=[x,y,w,h], polygon=[{x,y}...],
                 polygons=[[{x,y}...], ...], confidence} in ORIGINAL image
        coordinates.
    """
    prompts = list(vocab) if vocab is not None else list(ARCHITECTURAL_VOCAB)
    img = _to_pil(image)
    b64, sent_size, scale = _prepare_payload_image(img)
    payload = _post_sam3(b64, prompts)
    scale_back = 1.0 / scale if scale else 1.0
    dets = _parse_prompt_results(payload, scale_back)
    logger.info("SAM3 detect_objects: prompts=%d detections=%d sent_size=%s",
                len(prompts), len(dets), sent_size)
    return dets


def detect_materials_in_crop(
    image: Any,
    bbox: list[float] | tuple[float, float, float, float],
    material_vocab: Iterable[str],
) -> dict:
    """Pass 2 — material segmentation within a single object crop.

    Args:
        image: same accepted types as `detect_objects`, in ORIGINAL coords.
        bbox: [x, y, w, h] on the original image (from pass 1).
        material_vocab: prompt list e.g. ["painted wall", "wood paneling",
                        "tile"] — max 16.

    Returns:
        {
          "crop_origin": [x, y],           # pixel offset in original image
          "crop_size":   [w, h],           # pixel size of the crop sent
          "detections":  [ ... ],          # each with LOCAL crop coords AND
                                           # `bbox_global`, `polygon_global`
                                           # mapping back to the original.
        }
    """
    img = _to_pil(image)
    W, H = img.size
    x, y, w, h = [float(v) for v in bbox]
    x0 = max(0, int(round(x)))
    y0 = max(0, int(round(y)))
    x1 = min(W, int(round(x + w)))
    y1 = min(H, int(round(y + h)))
    if x1 <= x0 or y1 <= y0:
        raise Sam3Error(f"bbox {bbox} does not intersect the image ({W}x{H}).")
    crop = img.crop((x0, y0, x1, y1))
    b64, sent_size, scale = _prepare_payload_image(crop)
    payload = _post_sam3(b64, list(material_vocab))
    scale_back = 1.0 / scale if scale else 1.0
    local = _parse_prompt_results(payload, scale_back)

    # Attach original-image-space mirrors so callers can render without
    # having to redo the offset arithmetic.
    for d in local:
        if d["bbox"]:
            bx, by, bw, bh = d["bbox"]
            d["bbox_global"] = [bx + x0, by + y0, bw, bh]
        else:
            d["bbox_global"] = None
        if d.get("polygon"):
            d["polygon_global"] = [
                {"x": pt["x"] + x0, "y": pt["y"] + y0} for pt in d["polygon"]
            ]
        else:
            d["polygon_global"] = None
    logger.info(
        "SAM3 detect_materials_in_crop: crop_origin=(%d,%d) crop_size=%s "
        "prompts=%d detections=%d",
        x0, y0, (x1 - x0, y1 - y0), len(list(material_vocab)), len(local),
    )
    return {
        "crop_origin": [x0, y0],
        "crop_size": [x1 - x0, y1 - y0],
        "detections": local,
    }


def filter_detections(
    detections: list[dict],
    min_confidence: float = 0.55,
    iou_dedup: float = 0.70,
) -> list[dict]:
    """Drop sub-threshold detections and same-class near-duplicates.

    Rules (deliberately simple — no NMS across classes, no mask IoU, no
    box-in-box logic):
      1. Drop confidence < min_confidence.
      2. For each class, keep the highest-confidence detection first;
         drop any lower-confidence detection of the SAME class whose
         bounding-box IoU exceeds `iou_dedup`.
    """
    kept = [d for d in detections if float(d.get("confidence", 0)) >= min_confidence]
    kept.sort(key=lambda d: float(d.get("confidence", 0)), reverse=True)
    out: list[dict] = []
    for d in kept:
        if d.get("bbox") is None:
            out.append(d)
            continue
        clash = False
        for k in out:
            if k.get("label") != d.get("label") or k.get("bbox") is None:
                continue
            if _bbox_iou(k["bbox"], d["bbox"]) > iou_dedup:
                clash = True
                break
        if not clash:
            out.append(d)
    return out
