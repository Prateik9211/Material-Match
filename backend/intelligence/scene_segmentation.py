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

# Architectural-object vocabulary for Stage A.  Twenty-six prompts exceeds
# SAM3's 16-per-request cap, so `detect_objects` chunks the vocab and
# merges results — see that function for details.
#
# 2026-02-27 additions (wainscot / trim / paneled wainscoting) — closes
# the founder-reported gap where lower-wall paneling and moulding never
# surfaced as their own zones and got absorbed into "wall".  These three
# prompts are distinct enough at the SAM3 concept level that they don't
# just re-fire the plain "wall" mask.
#
# 2026-02-27 (round 4) — cushion / pillow / throw pillow / mattress
# added so SAM3 detects them explicitly on beds and sofas.  Their
# DETERMINISTIC_MATERIAL entry is `None` so they're SKIPPED from the
# materials pipeline — the parallel _run_products_pipeline LLM pass
# already identifies them as shoppable products.  Founder rule: never
# treat these as surface materials, always route to products.
# Headboards are NOT in this shortcut — they still go through normal
# material classification for fabric / wood / upholstery detection.
ARCHITECTURAL_VOCAB: tuple[str, ...] = (
    "wall", "ceiling", "floor", "cabinet", "countertop",
    "backsplash", "sofa", "curtain", "plant",
    "bed", "headboard", "mirror", "sink", "toilet", "bathtub",
    "rug", "shelf", "nightstand",
    "wainscot", "trim", "paneled wainscoting",
    "cushion", "pillow", "throw pillow", "mattress",
)


# Per-label minimum confidence overrides for `filter_detections`.  Large
# architectural surfaces (wall / ceiling / floor / backsplash) receive a
# lower gate than furniture and fixtures because SAM3's confidence tends
# to be soft on big, texture-poor regions — plain white ceilings and
# neutral hardwood floors regularly clock 0.42–0.52 even when the mask
# is a perfect fit.  Keeping the default 0.55 for everything else avoids
# regressing on false-positive fixtures.
LABEL_MIN_CONFIDENCE: dict[str, float] = {
    "wall":               0.40,
    "ceiling":            0.35,
    "floor":              0.35,
    "backsplash":         0.40,
    "wainscot":           0.40,
    "trim":               0.40,
    "paneled wainscoting": 0.40,
}


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
    # 2026-02-27 (round 4) — founder rule: cushions / pillows / mattresses
    # should NEVER be classified as surface materials.  They are shoppable
    # PRODUCTS, not surfaces, and the parallel _run_products_pipeline LLM
    # pass already targets them explicitly ("cushions, rugs, curtains…"
    # in PRODUCTS_USER_PROMPT), so we don't need to wire anything else —
    # just skip material classification here and the products section
    # picks them up automatically.
    # Headboards are intentionally NOT in this list — they remain in the
    # normal LLM material path so fabric / wood / upholstery still fires.
    "cushion": None,
    "pillow": None,
    "throw pillow": None,
    "mattress": None,
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
    cross_class_iou: float = 0.85,
    label_min_confidence: dict[str, float] | None = None,
) -> list[dict]:
    """Drop sub-threshold detections and dedup masks with heavy overlap.

    Two dedup passes:
      * Same-class:  detections with the SAME label sharing > `iou_dedup`
                     IoU keep only the higher-confidence one (default 0.70).
      * Cross-class: detections with DIFFERENT labels sharing very high
                     IoU (> `cross_class_iou`, default 0.85) keep only
                     the higher-confidence one — targets the SAM3
                     concept-overlap bug where "wall" and "backsplash"
                     fire on the same rectangular region with identical
                     bboxes.

    2026-02-27 — `label_min_confidence`: per-label override map (see
    module-level `LABEL_MIN_CONFIDENCE`).  Large architectural surfaces
    (wall / ceiling / floor / backsplash / wainscot / trim) frequently
    return sub-0.55 confidence on plain, texture-poor regions.  Using
    the same 0.55 gate as furniture and fixtures dropped ceilings and
    floors from live results.  The override loosens the gate ONLY for
    named labels; anything not in the map still uses `min_confidence`.
    """
    overrides = label_min_confidence or {}

    def _min_conf_for(label: str) -> float:
        return float(overrides.get((label or "").strip().lower(), min_confidence))

    kept = [
        d for d in detections
        if float(d.get("confidence", 0)) >= _min_conf_for(d.get("label", ""))
    ]
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
            if k.get("bbox") is None:
                continue
            iou = _bbox_iou(k["bbox"], d["bbox"])
            same_label = k.get("label") == d.get("label")
            if same_label and iou > iou_dedup:
                clash = True; break
            if not same_label and iou > cross_class_iou:
                clash = True; break
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


def _apply_polygon_mask(
    crop: Image.Image,
    polygon: list[dict[str, float]],
    crop_origin: tuple[int, int],
) -> Image.Image:
    """Replace pixels outside SAM3's polygon with the crop's median color.

    The median color is used (rather than pure black or white) because
    it minimizes the visual contrast between the masked-out region and
    the object surface — the DNA classifier can then focus on the
    object's material without being distracted by a hard mask edge or a
    contrasting background that the model might describe.

    Args:
        crop:        the axis-aligned bbox crop already extracted.
        polygon:     list of {"x": ..., "y": ...} points in ORIGINAL
                     image coordinates (as returned by Stage A).
        crop_origin: (x0, y0) offset of the crop within the original
                     image, so polygon coords can be translated into
                     crop-local coordinates.

    Returns:
        A new RGB PIL.Image with non-polygon pixels replaced by the
        crop's median colour.  Falls back to the untouched crop if the
        polygon degenerates or the mask fails to render.
    """
    from PIL import ImageDraw

    ox, oy = crop_origin
    cw, ch = crop.size
    local = []
    for p in polygon:
        try:
            x = float(p.get("x", 0)) - ox
            y = float(p.get("y", 0)) - oy
        except (TypeError, ValueError):
            continue
        local.append((x, y))
    if len(local) < 3:
        return crop
    try:
        mask = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask).polygon(local, fill=255)
    except (ValueError, TypeError):
        return crop

    # Sample the median color from ONLY the in-polygon pixels — if we
    # took the median of the whole crop, the background we're about to
    # mask out would drag the fill color.
    try:
        rgb = crop.convert("RGB")
        # Use a small sample to keep this fast — PIL's histogram over
        # 1M+ pixels is measurable on the request path.
        sample_edge = 128
        if max(cw, ch) > sample_edge:
            scale = sample_edge / max(cw, ch)
            sw, sh = max(1, int(cw * scale)), max(1, int(ch * scale))
            rgb_s = rgb.resize((sw, sh), Image.BILINEAR)
            mask_s = mask.resize((sw, sh), Image.NEAREST)
        else:
            rgb_s, mask_s = rgb, mask
        pixels = rgb_s.load()
        mpx = mask_s.load()
        rs, gs, bs = [], [], []
        for yy in range(rgb_s.height):
            for xx in range(rgb_s.width):
                if mpx[xx, yy] > 127:
                    r, g, b = pixels[xx, yy]
                    rs.append(r); gs.append(g); bs.append(b)
        if not rs:
            fill = (128, 128, 128)
        else:
            rs.sort(); gs.sort(); bs.sort()
            n = len(rs)
            fill = (rs[n // 2], gs[n // 2], bs[n // 2])
    except Exception:
        fill = (128, 128, 128)

    bg = Image.new("RGB", (cw, ch), fill)
    return Image.composite(crop.convert("RGB"), bg, mask)


async def classify_object_material(
    image: Any,
    bbox: list[float] | tuple[float, float, float, float],
    object_label: str,
    api_key: str,
    polygon: list[dict[str, float]] | None = None,
    object_confidence: float = 1.0,
    shortcut_min_confidence: float = 0.65,
) -> dict:
    """Stage B — material identification for a single detected object.

    Handles the three deterministic shortcuts (mirror / sink / faucet /
    plant) without hitting the LLM — but only when Stage-A confidence is
    high enough to trust the object label.  Below `shortcut_min_confidence`
    the mirror/sink/faucet shortcuts defer to the LLM path so a false-
    positive Stage-A detection doesn't get confidently mislabeled.

    For the LLM path, the crop is polygon-masked: pixels outside SAM3's
    reported polygon are filled with the crop's median color so the
    classifier never sees pixels that belong to a neighboring object.
    This eliminates the "wall bbox overlaps curtain → classifier flips
    to Fabric" family of failures.

    Args:
        image: source image (PIL / bytes / base64 / path).
        bbox:  [x, y, w, h] in original-image pixels (from Stage A).
        object_label: SAM3 object label (used both as `object_type_hint`
                      for the DNA prompt AND as the key for the
                      deterministic shortcut table).
        api_key: EMERGENT_LLM_KEY.
        polygon: optional list of {x, y} points in original-image pixels;
                 when present, pixels outside this polygon are masked out
                 of the crop before the LLM call.
        object_confidence: Stage-A confidence for this detection; used to
                 gate the deterministic shortcuts.
        shortcut_min_confidence: minimum confidence to trust a
                 mirror/sink/faucet shortcut (default 0.65).

    Returns:
        {
          "crop_origin": [x0, y0] | None,
          "crop_size":   [w, h]   | None,
          "source":      "shortcut" | "dna" | "skipped" | "error",
          "material":    <DNA dict>  or  <shortcut dict>  or  None,
          "error":       str  or  None,
        }
    """
    label_l = (object_label or "").strip().lower()

    # --- Shortcut: skip material entirely (plants). ----------------------
    # Plant → skip fires at ANY confidence; a skipped material is always
    # safer than a wrong one, and even a low-confidence plant detection
    # is very unlikely to have useful material info.
    if label_l in DETERMINISTIC_MATERIAL and DETERMINISTIC_MATERIAL[label_l] is None:
        return {
            "crop_origin": None, "crop_size": None,
            "source": "skipped",
            "material": None,
            "error": f"no meaningful material for object type '{label_l}'",
        }

    # --- Shortcut: deterministic material (mirror/sink/faucet). ----------
    # Only trust when Stage-A confidence is high enough; below the gate,
    # fall through to the LLM path so g10-style false-positive "sinks"
    # on exterior walls don't get confidently mislabeled as Metal.
    if (
        label_l in DETERMINISTIC_MATERIAL
        and DETERMINISTIC_MATERIAL[label_l] is not None
        and float(object_confidence) >= float(shortcut_min_confidence)
    ):
        return {
            "crop_origin": None, "crop_size": None,
            "source": "shortcut",
            "material": dict(DETERMINISTIC_MATERIAL[label_l]),
            "error": None,
        }

    # --- LLM path: crop + generate_swatch_dna. ---------------------------
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
        # Polygon mask: replace non-object pixels with the crop's median
        # color so the classifier only reasons about pixels SAM3 assigned
        # to this object.  Skip masking if the polygon is missing or so
        # coarse it barely differs from the bbox (in which case the mask
        # has no effect anyway).
        if polygon and len(polygon) >= 3:
            crop = _apply_polygon_mask(crop, polygon, (x0, y0))
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
