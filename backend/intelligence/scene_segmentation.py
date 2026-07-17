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

# Architectural-object vocabulary for pass 1.
#
# Feb 2026 expansion — added the objects the founder confirmed as missing
# during manual /admin/scene-test runs:
#   bed, headboard, mirror, sink, toilet, bathtub, rug, carpet, shelf,
#   nightstand.
# Nineteen prompts exceeds SAM3's 16-per-request cap, so `detect_objects`
# chunks the vocab and merges results — see that function for details.
#
# 21-image sweep follow-up — `carpet` removed: it duplicated every `rug`
# detection (identical concept in SAM3's text space, guaranteed double
# count) and false-fired on bare-wood scenes. `rug` covers the concept.
ARCHITECTURAL_VOCAB: tuple[str, ...] = (
    "wall", "ceiling", "floor", "cabinet", "countertop",
    "backsplash", "sofa", "curtain", "plant",
    "bed", "headboard", "mirror", "sink", "toilet", "bathtub",
    "rug", "shelf", "nightstand",
)


# Object-label → material vocabulary override.
#
# Feb 2026 fix — the flat DEFAULT_MATERIAL_VOCAB in the endpoint was applied
# to EVERY detected object, which forced things like "plant" and "mirror"
# through prompts like "painted wall / wood paneling / tile", yielding
# nonsense sub-detections. This map narrows Stage-B to a sensible subset
# per object type; value `None` means "skip Stage-B entirely for this
# object type". Objects not present in the map fall through to the caller's
# default vocab (`_DEFAULT_MATERIAL_VOCAB` in server.py).
MATERIAL_VOCAB_BY_OBJECT: dict[str, tuple[str, ...] | None] = {
    "plant":     None,
    "mirror":    ("glass panel", "metal fixture"),
    "sink":      ("metal fixture", "stone slab", "tile"),
    "toilet":    ("metal fixture", "glass panel"),
    "bathtub":   ("stone slab", "tile", "metal fixture"),
    "curtain":   ("fabric upholstery",),
    "sofa":      ("fabric upholstery", "wood paneling", "metal fixture"),
    "rug":       ("fabric upholstery",),
    "bed":       ("fabric upholstery", "wood paneling"),
    "headboard": ("fabric upholstery", "wood paneling"),
    # cabinet / wall / ceiling / floor / countertop / backsplash / shelf /
    # nightstand are NOT in the map — they fall through to the full
    # material-surface vocabulary from the caller.
}


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
    """Flatten Roboflow SAM3's `prompt_results` into a list of detections.

    Real Roboflow SAM3 schema (verified against a live call):
      {
        "prompt_results": [
          {
            "prompt_index": 0,
            "echo": {"type": "text", "text": "cabinet", "num_boxes": 0},
            "predictions": [
              {
                "masks": [ [[x, y], [x, y], ...], [[x, y], ...] ],  # list of contours
                "confidence": 0.7539,
                "format": "polygon"
              }, ...
            ]
          }, ...
        ],
        "time": ...
      }

    `scale_back` (>= 1.0) multiplies polygon and bbox coordinates so they
    map to the caller's ORIGINAL (pre-downscale) image space.
    """
    detections: list[dict] = []
    prompt_results = payload.get("prompt_results") or []
    for pr in prompt_results:
        echo = pr.get("echo") or {}
        label = echo.get("text") or pr.get("prompt") or pr.get("class") or "?"
        if isinstance(label, dict):
            label = label.get("text", "?")
        preds = pr.get("predictions") or []
        for p in preds:
            conf = float(p.get("confidence", p.get("score", 0.0)) or 0.0)
            # Roboflow returns `masks`: a list of contours, each a list of
            # [x, y] pairs.
            contours = p.get("masks") or []
            all_contours: list[list[dict[str, float]]] = []
            for contour in contours:
                if not contour:
                    continue
                all_contours.append([_scale_point(pt, scale_back) for pt in contour])
            primary = max(all_contours, key=len) if all_contours else []
            bbox = _polygon_bbox(primary) if primary else None
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

    Notes:
        Roboflow SAM3 caps at 16 prompts per request. When `vocab` exceeds
        that we chunk into ≤16-prompt requests against the SAME encoded
        image and concatenate results. The image bytes are re-uploaded per
        chunk (Roboflow serverless has no session reuse) — cost = ceil(N/16)
        SAM3 calls.
    """
    prompts = list(vocab) if vocab is not None else list(ARCHITECTURAL_VOCAB)
    img = _to_pil(image)
    b64, sent_size, scale = _prepare_payload_image(img)
    scale_back = 1.0 / scale if scale else 1.0
    dets: list[dict] = []
    for i in range(0, len(prompts), SAM3_MAX_PROMPTS):
        chunk = prompts[i:i + SAM3_MAX_PROMPTS]
        payload = _post_sam3(b64, chunk)
        dets.extend(_parse_prompt_results(payload, scale_back))
    logger.info("SAM3 detect_objects: prompts=%d chunks=%d detections=%d sent_size=%s",
                len(prompts), (len(prompts) + SAM3_MAX_PROMPTS - 1) // SAM3_MAX_PROMPTS,
                len(dets), sent_size)
    return dets


def _max_pad_in_direction(
    direction: str,
    bbox: tuple[float, float, float, float],
    others: list[tuple[float, float, float, float]],
    W: int, H: int,
) -> float:
    """Maximum pixel distance we can expand `bbox` in one direction before
    entering either the image boundary OR another object's bbox territory.

    A neighbor is a "blocker" for direction `d` when it has any pixel on
    the side of `bbox` where we're trying to expand AND its perpendicular
    range overlaps ours. Blockers cap the padding at the gap between our
    edge and the nearest blocker's edge. If a blocker already OVERLAPS the
    current bbox (i.e. crosses our edge in the same direction we're
    expanding), the max pad becomes 0 — don't fight for shared space.
    """
    bx, by, bw, bh = bbox
    if direction == "up":
        limit = by
        for nx, ny, nw, nh in others:
            if nx + nw <= bx or nx >= bx + bw:   # no horizontal overlap
                continue
            if ny >= by:                          # entirely below our top
                continue
            n_bottom = ny + nh
            if n_bottom <= by:                    # sits cleanly above; gap
                limit = min(limit, by - n_bottom)
            else:                                 # crosses our top → 0
                return 0.0
        return max(0.0, limit)
    if direction == "down":
        bottom = by + bh
        limit = H - bottom
        for nx, ny, nw, nh in others:
            if nx + nw <= bx or nx >= bx + bw:
                continue
            if ny + nh <= bottom:                 # entirely above our bottom
                continue
            if ny >= bottom:                      # sits cleanly below
                limit = min(limit, ny - bottom)
            else:
                return 0.0
        return max(0.0, limit)
    if direction == "left":
        limit = bx
        for nx, ny, nw, nh in others:
            if ny + nh <= by or ny >= by + bh:
                continue
            if nx >= bx:
                continue
            n_right = nx + nw
            if n_right <= bx:
                limit = min(limit, bx - n_right)
            else:
                return 0.0
        return max(0.0, limit)
    if direction == "right":
        right = bx + bw
        limit = W - right
        for nx, ny, nw, nh in others:
            if ny + nh <= by or ny >= by + bh:
                continue
            if nx + nw <= right:
                continue
            if nx >= right:
                limit = min(limit, nx - right)
            else:
                return 0.0
        return max(0.0, limit)
    return 0.0


def detect_materials_in_crop(
    image: Any,
    bbox: list[float] | tuple[float, float, float, float],
    material_vocab: Iterable[str],
    pad_thin_bbox: bool = True,
    other_bboxes: Iterable[list[float] | tuple[float, float, float, float]] | None = None,
) -> dict:
    """Pass 2 — material segmentation within a single object crop.

    Args:
        image: same accepted types as `detect_objects`, in ORIGINAL coords.
        bbox: [x, y, w, h] on the original image (from pass 1).
        material_vocab: prompt list e.g. ["painted wall", "wood paneling",
                        "tile"] — max 16.
        pad_thin_bbox: when True (default), a bbox whose SHORT edge is <15%
                       of the image's short edge is padded outward before
                       cropping so SAM3 has enough spatial context to
                       classify materials (fixes the "tile between cabinets"
                       backsplash-strip dropout).
        other_bboxes: optional list of sibling object bboxes (from the same
                      detect_objects call). When provided, padding is
                      clipped so it never expands into another already-
                      detected object's territory. Prevents cross-object
                      material bleed (e.g. floor padding upward into
                      cabinet space and returning "wood paneling").

    Returns:
        {
          "crop_origin": [x, y],           # pixel offset in original image
                                           # (of the ACTUAL crop sent, i.e.
                                           # AFTER any thin-bbox padding).
          "crop_size":   [w, h],           # pixel size of the crop sent.
          "bbox_padded": bool,             # True if thin-bbox padding fired.
          "pad_applied": {up,down,left,right},  # per-side px actually padded.
          "detections":  [ ... ],          # each with LOCAL crop coords AND
                                           # `bbox_global`, `polygon_global`
                                           # mapping back to the original.
        }
    """
    img = _to_pil(image)
    W, H = img.size
    x, y, w, h = [float(v) for v in bbox]

    # Normalize sibling bboxes and remove any that are byte-identical to ours
    # (defensive: caller might forget to exclude self).
    sibs: list[tuple[float, float, float, float]] = []
    if other_bboxes:
        for ob in other_bboxes:
            if not ob or len(ob) < 4:
                continue
            t = tuple(float(v) for v in ob[:4])
            if t == (x, y, w, h):
                continue
            sibs.append(t)

    # ------------------------------------------------------------------
    # Thin-bbox padding, now NEIGHBOR-AWARE.
    #   1. Trigger when short edge < 15% of image short edge.
    #   2. Target: extend short edge to reach 25% of image short edge.
    #   3. Split the required extra 50/50 between the two sides of the
    #      short axis, but cap each side by the max-safe-pad computed
    #      against sibling bboxes. Unused half is redistributed to the
    #      other side (if that side has room). If total available space
    #      is less than target, accept the tighter crop — never bleed.
    # ------------------------------------------------------------------
    padded = False
    pad_up = pad_down = pad_left = pad_right = 0.0
    if pad_thin_bbox:
        THIN_THRESHOLD_FRAC = 0.15
        TARGET_SHORT_EDGE_FRAC = 0.25
        img_short = min(W, H)
        bbox_short = min(w, h)
        if bbox_short > 0 and bbox_short / img_short < THIN_THRESHOLD_FRAC:
            target = TARGET_SHORT_EDGE_FRAC * img_short
            cur_bbox = (x, y, w, h)
            if w <= h:  # short axis is width → pad left/right
                extra = max(0.0, target - w)
                half = extra / 2
                max_l = _max_pad_in_direction("left", cur_bbox, sibs, W, H)
                max_r = _max_pad_in_direction("right", cur_bbox, sibs, W, H)
                pad_left = min(half, max_l)
                pad_right = min(extra - pad_left, max_r)
                # Redistribute unused left-room to right and vice versa.
                if pad_left + pad_right < extra and pad_left < max_l:
                    pad_left = min(max_l, extra - pad_right)
                x -= pad_left
                w += pad_left + pad_right
            else:       # short axis is height → pad up/down
                extra = max(0.0, target - h)
                half = extra / 2
                max_u = _max_pad_in_direction("up", cur_bbox, sibs, W, H)
                max_d = _max_pad_in_direction("down", cur_bbox, sibs, W, H)
                pad_up = min(half, max_u)
                pad_down = min(extra - pad_up, max_d)
                if pad_up + pad_down < extra and pad_up < max_u:
                    pad_up = min(max_u, extra - pad_down)
                y -= pad_up
                h += pad_up + pad_down
            padded = (pad_up + pad_down + pad_left + pad_right) > 0

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
        "prompts=%d detections=%d padded=%s pad_udlr=(%d,%d,%d,%d)",
        x0, y0, (x1 - x0, y1 - y0), len(list(material_vocab)), len(local),
        padded, pad_up, pad_down, pad_left, pad_right,
    )
    return {
        "crop_origin": [x0, y0],
        "crop_size": [x1 - x0, y1 - y0],
        "bbox_padded": padded,
        "pad_applied": {
            "up": round(pad_up), "down": round(pad_down),
            "left": round(pad_left), "right": round(pad_right),
        },
        "detections": local,
    }


def filter_detections(
    detections: list[dict],
    min_confidence: float = 0.55,
    iou_dedup: float = 0.70,
    min_area_frac: float = 0.0,
    image_w: int | None = None,
    image_h: int | None = None,
) -> list[dict]:
    """Drop sub-threshold detections and same-class near-duplicates.

    Rules (deliberately simple — no NMS across classes, no mask IoU, no
    box-in-box logic):
      1. Drop confidence < min_confidence.
      2. Drop bbox_area / (image_w * image_h) < min_area_frac  when the
         image size is provided and min_area_frac > 0. Kills tiny
         decorative slat / pendant / cubby-edge clutter that survives the
         confidence gate. 21-image sweep found ~5-11 spurious `shelf`
         detections per image at 0.05-0.4% area — 0.005 (=0.5%) removes
         them without touching legitimate small objects like mirrors.
      3. For each class, keep the highest-confidence detection first;
         drop any lower-confidence detection of the SAME class whose
         bounding-box IoU exceeds `iou_dedup`.
    """
    kept = [d for d in detections if float(d.get("confidence", 0)) >= min_confidence]
    if min_area_frac > 0 and image_w and image_h:
        img_area = float(image_w) * float(image_h)
        area_gated: list[dict] = []
        for d in kept:
            b = d.get("bbox") or None
            if not b or img_area <= 0:
                area_gated.append(d)
                continue
            if (float(b[2]) * float(b[3])) / img_area >= min_area_frac:
                area_gated.append(d)
        kept = area_gated
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
