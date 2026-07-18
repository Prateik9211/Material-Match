"""Scene segmentation — hybrid SAM3 (Stage A) + GPT-4o-mini (Stage B).

Two-pass strategy:

  Stage A — `detect_objects(image)` (unchanged)
      Send the whole room photo to Roboflow SAM3 with a fixed
      architectural vocabulary ("wall", "cabinet", "countertop", ...).
      SAM3 returns one segmentation per prompt with polygon masks and
      confidence scores.

  Stage B — `classify_object_material(image, bbox, object_label)` (NEW)
      Crop the original image to the object's bbox and run material
      identification through the SAME production function the live
      matcher uses: `intelligence.dna.generate_swatch_dna` (GPT-4o-mini
      via `EMERGENT_LLM_KEY`).  This produces a full Visual DNA dict
      (material_family, surface_type, primary_color, pattern, finish,
      gloss_level, canonical_description, ...) instead of SAM3's
      single-label + mask output.

      For three well-behaved object types we skip the LLM call entirely
      because a deterministic answer is more accurate AND free:
          mirror        → Glass panel
          sink | faucet → Metal fixture
          plant         → skip material (no useful material to report)

Post-processing — `filter_detections(detections, min_confidence)` (unchanged)
      Drops sub-threshold Stage-A detections and dedups masks with
      heavy overlap.

Requires:
    ROBOFLOW_API_KEY   — Stage A (SAM3 hosted)
    EMERGENT_LLM_KEY   — Stage B (GPT-4o-mini via emergentintegrations)
"""
from __future__ import annotations

import asyncio
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
SAM3_MAX_LONGEST_EDGE = 1024
SAM3_MAX_BYTES = 2 * 1024 * 1024
SAM3_MAX_PROMPTS = 16

# Stage-B (GPT-4o-mini via generate_swatch_dna) config — mirrors the live
# matcher's defaults so the SAM3 debug tool exercises the same code path.
VISUAL_DNA_PROVIDER = os.environ.get("VISUAL_DNA_PROVIDER", "openai")
VISUAL_DNA_MODEL = os.environ.get("VISUAL_DNA_MODEL", "gpt-4o-mini")
DNA_TIMEOUT_S = int(os.environ.get("VISUAL_DNA_TIMEOUT_S", "45"))

# Architectural-object vocabulary for Stage A.  Nineteen prompts exceeds
# SAM3's 16-per-request cap, so `detect_objects` chunks the vocab and
# merges results — see that function for details.
ARCHITECTURAL_VOCAB: tuple[str, ...] = (
    "wall", "ceiling", "floor", "cabinet", "countertop",
    "backsplash", "sofa", "curtain", "plant",
    "bed", "headboard", "mirror", "sink", "toilet", "bathtub",
    "rug", "shelf", "nightstand",
)


# Deterministic shortcuts — no LLM call needed for these object types.
#   value = the material result to return
#   value == None → skip material entirely
DETERMINISTIC_MATERIAL: dict[str, dict | None] = {
    "mirror": {
        "material_family": "Glass",
        "surface_type": "mirror glass",
        "primary_color": {"name": "reflective", "hex": ""},
        "pattern": "plain solid",
        "finish": "polished",
        "gloss_level": "high",
        "canonical_description": "Reflective mirror glass panel.",
        "source": "shortcut",
    },
    "sink": {
        "material_family": "Metal",
        "surface_type": "metal fixture",
        "primary_color": {"name": "silver", "hex": "#C0C0C0"},
        "pattern": "plain solid",
        "finish": "brushed",
        "gloss_level": "medium",
        "canonical_description": "Metal plumbing fixture, typical sink/basin surface.",
        "source": "shortcut",
    },
    "faucet": {
        "material_family": "Metal",
        "surface_type": "metal fixture",
        "primary_color": {"name": "silver", "hex": "#C0C0C0"},
        "pattern": "plain solid",
        "finish": "brushed",
        "gloss_level": "medium",
        "canonical_description": "Metal plumbing fixture, typical faucet/tap surface.",
        "source": "shortcut",
    },
    "plant": None,
}


class Sam3Error(RuntimeError):
    """Any failure originating from the SAM3 call — missing key, bad
    response, network error, or the API returning a non-2xx status."""


# ---------------------------------------------------------------------------
# Internal helpers (Stage A — unchanged from previous SAM3-only build)
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
    """Downscale to Roboflow's 1024/2MB limits, return (base64, (W,H), scale)."""
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


def _scale_point(pt: Any, scale_back: float) -> dict[str, float]:
    if isinstance(pt, dict):
        return {"x": float(pt.get("x", 0)) * scale_back,
                "y": float(pt.get("y", 0)) * scale_back}
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        return {"x": float(pt[0]) * scale_back,
                "y": float(pt[1]) * scale_back}
    return {"x": 0.0, "y": 0.0}


def _parse_prompt_results(payload: dict, scale_back: float) -> list[dict]:
    """Flatten Roboflow SAM3's `prompt_results` into a list of detections."""
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
# Stage A — Public API (unchanged)
# ---------------------------------------------------------------------------
def detect_objects(image: Any, vocab: Iterable[str] | None = None) -> list[dict]:
    """Stage A — architectural-object detection via SAM3.

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


def filter_detections(
    detections: list[dict],
    min_confidence: float = 0.55,
    iou_dedup: float = 0.70,
    min_area_frac: float = 0.0,
    image_w: int | None = None,
    image_h: int | None = None,
) -> list[dict]:
    """Drop sub-threshold detections and same-class near-duplicates."""
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


# ---------------------------------------------------------------------------
# Stage B — Material classification via generate_swatch_dna (GPT-4o-mini)
# ---------------------------------------------------------------------------
def _crop_to_bbox(img: Image.Image, bbox: list[float]) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Return (crop, (x0, y0, x1, y1)) with bbox in [x, y, w, h] format."""
    W, H = img.size
    x, y, w, h = [float(v) for v in bbox]
    x0 = max(0, int(round(x)))
    y0 = max(0, int(round(y)))
    x1 = min(W, int(round(x + w)))
    y1 = min(H, int(round(y + h)))
    if x1 <= x0 or y1 <= y0:
        raise Sam3Error(f"bbox {bbox} does not intersect the image ({W}x{H}).")
    return img.crop((x0, y0, x1, y1)), (x0, y0, x1, y1)


def _crop_to_base64(crop: Image.Image, max_edge: int = 1024) -> str:
    """Encode crop as base64 JPEG.  Downscale huge crops so the vision
    API doesn't burn tokens on unnecessary pixels."""
    w, h = crop.size
    longest = max(w, h)
    if longest > max_edge:
        s = max_edge / longest
        crop = crop.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=90, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def classify_object_material(
    image: Any,
    bbox: list[float] | tuple[float, float, float, float],
    object_label: str,
    api_key: str,
) -> dict:
    """Stage B — material identification for a single detected object.

    Handles the three deterministic shortcuts (mirror / sink / faucet /
    plant) without hitting the LLM.  For everything else, crops the image
    to the object's bbox and calls the production
    `intelligence.dna.generate_swatch_dna` function — the same call the
    live matcher uses on every user-region query.

    Args:
        image: source image (PIL / bytes / base64 / path).
        bbox:  [x, y, w, h] in original-image pixels (from Stage A).
        object_label: SAM3 object label (used both as `object_type_hint`
                      for the DNA prompt AND as the key for the
                      deterministic shortcut table).
        api_key: EMERGENT_LLM_KEY.

    Returns:
        {
          "crop_origin": [x0, y0] | None,   # pixel offset in original.
          "crop_size":   [w, h]   | None,   # size of the crop sent.
          "source":      "shortcut" | "dna" | "skipped" | "error",
          "material":    <DNA dict>  or  <shortcut dict>  or  None,
          "error":       str  or  None,
        }
    """
    label_l = (object_label or "").strip().lower()

    # --- Shortcut: skip material entirely (plants). -----------------------
    if label_l in DETERMINISTIC_MATERIAL and DETERMINISTIC_MATERIAL[label_l] is None:
        return {
            "crop_origin": None, "crop_size": None,
            "source": "skipped",
            "material": None,
            "error": f"no meaningful material for object type '{label_l}'",
        }

    # --- Shortcut: deterministic material (mirror/sink/faucet). -----------
    if label_l in DETERMINISTIC_MATERIAL:
        return {
            "crop_origin": None, "crop_size": None,
            "source": "shortcut",
            "material": dict(DETERMINISTIC_MATERIAL[label_l]),
            "error": None,
        }

    # --- LLM path: crop + generate_swatch_dna. ----------------------------
    if not api_key:
        return {
            "crop_origin": None, "crop_size": None,
            "source": "error",
            "material": None,
            "error": "EMERGENT_LLM_KEY missing — cannot run Stage-B DNA call",
        }

    try:
        img = _to_pil(image)
        crop, (x0, y0, x1, y1) = _crop_to_bbox(img, list(bbox))
    except Sam3Error as e:
        return {
            "crop_origin": None, "crop_size": None,
            "source": "error", "material": None, "error": str(e),
        }

    b64 = _crop_to_base64(crop)
    metadata = {
        "detected_color": "",
        "detected_finish": "",
        "object_type_hint": label_l,
    }
    from intelligence.dna import generate_swatch_dna
    try:
        dna = await asyncio.wait_for(
            generate_swatch_dna(
                b64, metadata, api_key, VISUAL_DNA_PROVIDER, VISUAL_DNA_MODEL,
                timeout_s=DNA_TIMEOUT_S,
            ),
            timeout=DNA_TIMEOUT_S + 5,
        )
    except asyncio.TimeoutError:
        return {
            "crop_origin": [x0, y0], "crop_size": [x1 - x0, y1 - y0],
            "source": "error", "material": None,
            "error": f"generate_swatch_dna timed out after {DNA_TIMEOUT_S}s",
        }
    if not dna:
        return {
            "crop_origin": [x0, y0], "crop_size": [x1 - x0, y1 - y0],
            "source": "error", "material": None,
            "error": "generate_swatch_dna returned no DNA (LLM parse failure)",
        }
    return {
        "crop_origin": [x0, y0], "crop_size": [x1 - x0, y1 - y0],
        "source": "dna",
        "material": {**dna, "source": "dna"},
        "error": None,
    }
