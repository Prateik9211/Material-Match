"""Visual DNA — the single canonical material representation used on BOTH
sides of the matcher (catalogue swatches and user reference regions).

A Visual DNA dict always has the same keys regardless of where it came
from (vision enrichment, record metadata, or query-time analysis row), so
retrieval compares like with like.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

DNA_VERSION = 1

DNA_KEYS = (
    "material_family", "surface_type", "primary_color", "secondary_colors",
    "color_temperature", "texture", "pattern", "pattern_scale",
    "finish", "gloss_level", "typical_applications", "canonical_description",
)

SWATCH_DNA_SYSTEM = (
    "You are a materials specification expert for architects. You are shown "
    "ONE isolated material swatch image from a supplier catalogue. Describe "
    "its visual identity precisely so it can be matched against photographs "
    "of real interiors. Reply with ONLY valid JSON, no markdown."
)

SWATCH_DNA_PROMPT = """Known metadata from the upstream classifier (MAY be wrong — treat as a weak hint, NEVER as authoritative):
{metadata}

Family selection is driven by VISUAL EVIDENCE in the image, not by the metadata.

Primary rules (image-first):
- Plain uniform surface with NO grain, NO pattern, NO texture, NO veining, NO grout, NO weave → **Paint**, regardless of what the classifier said the object was.
- Visible wood grain (linear or figured) + rigid flat surface → **Laminate** or **Veneer** (**Wood** only if clearly solid timber).
- Woven / knitted / textile texture on a soft, draped or cushioned surface (cushion, sofa seat, curtain, rug, throw) → **Fabric**.
- Woven / cane / rattan pattern on a rigid hard surface (wardrobe door, wall panel, cabinet front) → **Laminate** (cane-look laminates are common — NOT Fabric).
- Stone veining and polished / satin shine + rigid surface → **Stone**.
- Small repeating tiles with visible grout joints → **Tile**.
- Metallic sheen + no wood grain / no fabric weave → **Metal**.
- Drywall / plaster / gypsum / POP surface → **Paint**.

Metadata should influence family ONLY when visual evidence is genuinely ambiguous. If the classifier's object_type contradicts what the image plainly shows, TRUST THE IMAGE.

AMBIENT LIGHT NORMALISATION (critical — do this BEFORE reporting colour):
Interior photographs are almost always lit by warm tungsten / halogen / LED lamps or cool daylight streaming through a window. That lighting tints EVERY surface in the frame the same direction. Before you name the material's colour, mentally subtract that ambient tint.
- If the crop is a CEILING, WALL, TRIM, MOULDING, WAINSCOT, DOOR or DRYWALL surface that reads warm/creamy/peach/terracotta/tan but shows NO visible grain, weave, veining or pattern → the surface is almost certainly WHITE or OFF-WHITE PAINT being tinted by warm ambient light. Report primary_color as "white" or "off-white" (hex around #F5F1EA to #FFFFFF), NEVER "terracotta", "warm peach", "beige paint" etc. Set color_temperature to "neutral" — do NOT let the room's warm cast leak into the swatch reading.
- If the crop is a FLOOR or CEILING with a strong uniform blue/green cast but shows no pigment pattern → likely a neutral surface (white/grey) under cool daylight; normalise the same way.
- Only trust a warm/cool cast as the material's actual colour when it is IN CONFLICT with the ambient direction — e.g. a cool blue panel photographed under warm light, or a warm terracotta tile under cool daylight. Those contrasts confirm the pigment is real.
- Rule of thumb: if the crop's cast matches the dominant cast of the wider scene it came from, treat it as ambient and neutralise. If it pushes back against the ambient direction, treat it as pigment.

IMPORTANT — pattern defaults for wood families: when material_family is Laminate, Veneer or Wood, DEFAULT the `pattern` field to a wood-grain description (e.g. "linear wood grain", "figured wood grain") unless the surface is UNAMBIGUOUSLY plain solid (a smooth uniform colour panel with zero grain even at high magnification, e.g. an acrylic solid-colour cabinet front). A small crop of a warm-brown wooden beam / plank / door should still report a wood-grain pattern even when fine grain is not resolvable at the crop's pixel resolution. This is critical so the retrieval embedding retrieves wood-grain catalogue candidates, not solid-colour panels.

OBJECT-AWARE FAMILY BIAS (cabinets/joinery, added 2026-02-05 round 7):
When the incoming object cue (`object_type`) is one of `cabinet`, `cupboard`, `cabinetry`, `wardrobe`, `built-in`, `door`, `drawer`, or `kitchen island`, AND the surface reads as plain uniform solid colour with NO visible paint-application artefacts (no roller stipple, no visible cutting-in edges at reveals/mitres, no brush marks, no paint-drip texture), STRONGLY PREFER `material_family="Laminate"` over `material_family="Paint"`. Modern Indian kitchen/bedroom cabinetry, wardrobes and joinery are almost universally faced with decorative laminate, PU-lacquered MDF or veneer — not with painted timber. Only report `Paint` on a cabinet-family object when you can visibly identify paint-application artefacts. This rule is SCOPED to cabinet-family objects; walls, ceilings, trims and doors-set-into-walls continue to follow the plain-uniform → Paint default above.

Choose "Other" ONLY when the image genuinely does not fit any listed family; never as a lazy default.

CANONICAL DESCRIPTION rules (critical for embedding retrieval):
- Describe ONLY the surface material — its colour, grain/pattern, texture and finish.
- Do NOT mention object SHAPE (arches, cutouts, curves, edges, legs), object FUNCTION (bench, headboard, wardrobe) or STYLE labels (modern, minimalist, contemporary). These pollute the material embedding.
- Focus on words a laminate/tile/fabric supplier would use in a spec sheet.

Return exactly this JSON:
{{
  "material_family": "one of: Laminate | Veneer | Tile | Paint | Stone | Fabric | Wood | Metal | Wallpaper | Other",
  "family_confidence": "0.0-1.0 — how confident you are the primary family is correct. Rules for LOW confidence (<0.55): (a) the crop is a plain uniform panel with NO texture, grain, veining, weave, or grout visible AND the colour is a wood-tone warm brown / tan / oak / walnut / caramel — in that case a plausible Laminate or Veneer or Wood cannot be ruled out by texture and you MUST report LOW confidence with those as alternatives; (b) small (<200px) low-resolution crop where fine grain / weave would not be resolvable; (c) glossy dark panel that could be Laminate, Paint, or Stone. Rules for HIGH confidence (>=0.8): visible grain, weave, veining, grout / tile joints, or an unambiguous fabric drape.",
  "family_alternatives": ["1-2 other plausible families in order of likelihood — REQUIRED when family_confidence < 0.7. Common pairs: a warm-brown flat patch → ['Laminate','Wood']; a dark glossy panel → ['Laminate','Stone']; a white matte panel → ['Laminate','Paint']. Empty [] only when family_confidence >= 0.7. Never include the primary family here."],
  "surface_type": "specific surface e.g. 'wood-grain decorative laminate', 'polished marble slab', 'cane-look laminate panel'",
  "primary_color": {{"name": "short colour name", "hex": "#RRGGBB"}},
  "secondary_colors": [{{"name": "...", "hex": "#RRGGBB"}}],
  "color_temperature": "warm | cool | neutral",
  "texture": "short texture description e.g. 'straight grain, medium contrast'",
  "pattern": "short pattern description e.g. 'linear woodgrain, vertical' or 'plain solid'",
  "pattern_scale": "fine | medium | large | none",
  "finish": "matte | satin | gloss | textured | brushed | polished",
  "gloss_level": "low | medium | high",
  "typical_applications": ["2-4 surfaces this is typically used on, e.g. 'kitchen cabinet front', 'feature wall'"],
  "canonical_description": "ONE sentence, <=40 words, describing colour, pattern, texture and finish as a spec sheet would — NEVER mention object shape, cutouts, edges, legs, function or style."
}}"""


def _clean_str(v) -> str:
    return str(v).strip() if v not in (None, "", "None") else ""


def _norm_color(c) -> dict:
    if isinstance(c, dict):
        return {"name": _clean_str(c.get("name")), "hex": _clean_str(c.get("hex"))}
    return {"name": _clean_str(c), "hex": ""}


def normalize_dna(raw: dict) -> dict:
    """Coerce any LLM/metadata output into the canonical DNA shape."""
    raw = raw or {}
    # Family alternatives — parsed for low-confidence crops so retrieval
    # can widen the candidate pool to include a second/third plausible
    # family instead of committing to a single guessed family and
    # searching the wrong catalogue category.
    alts_raw = raw.get("family_alternatives") or []
    if isinstance(alts_raw, str):
        alts_raw = [alts_raw]
    family_alternatives: list[str] = []
    for a in alts_raw[:3]:
        s = _clean_str(a).title()
        if s and s not in family_alternatives:
            family_alternatives.append(s)
    try:
        family_confidence = float(raw.get("family_confidence") or 1.0)
    except (TypeError, ValueError):
        family_confidence = 1.0
    family_confidence = max(0.0, min(1.0, family_confidence))

    dna = {
        "material_family": _clean_str(raw.get("material_family")).title(),
        "family_confidence": family_confidence,
        "family_alternatives": family_alternatives,
        "surface_type": _clean_str(raw.get("surface_type")),
        "primary_color": _norm_color(raw.get("primary_color")),
        "secondary_colors": [_norm_color(c) for c in (raw.get("secondary_colors") or [])[:3]],
        "color_temperature": _clean_str(raw.get("color_temperature")).lower(),
        "texture": _clean_str(raw.get("texture")),
        "pattern": _clean_str(raw.get("pattern")),
        "pattern_scale": _clean_str(raw.get("pattern_scale")).lower(),
        "finish": _clean_str(raw.get("finish")),
        "gloss_level": _clean_str(raw.get("gloss_level")).lower(),
        "typical_applications": [_clean_str(a) for a in (raw.get("typical_applications") or [])[:4] if _clean_str(a)],
        "canonical_description": _clean_str(raw.get("canonical_description")),
        "dna_version": DNA_VERSION,
    }
    # Never let an alt echo the primary.
    fam_l = dna["material_family"].lower()
    dna["family_alternatives"] = [a for a in dna["family_alternatives"] if a.lower() != fam_l]

    # 2026-07 heuristic — DNA classifiers are prone to committing to
    # "Paint" for any flat uniform crop, but real interior paint colours
    # don't cover the warm caramel / tan / oak / walnut range (those
    # colours read as Laminate or Wood in a real spec book).  When we
    # see `Paint` on a warm-brown crop, override to low confidence and
    # add the missing alternatives so retrieval also searches the
    # laminate and wood catalogues.  This directly fixes the T4-class
    # failure: an isolated warm-oak swatch getting Paint-family retrieval
    # instead of Wood/Laminate.
    if fam_l == "paint":
        pc_hex = (dna.get("primary_color") or {}).get("hex") or ""
        r = g = b = None
        if pc_hex.startswith("#") and len(pc_hex) == 7:
            try:
                r = int(pc_hex[1:3], 16); g = int(pc_hex[3:5], 16); b = int(pc_hex[5:7], 16)
            except ValueError:
                r = g = b = None
        # Warm-brown / tan / oak / walnut range: R > G > B AND R-B > 40
        # AND R > 100 AND B < 180 (excludes bright whites/creams that
        # ARE plausible paint colours).
        if (r is not None and r > g > b and (r - b) > 40
                and r > 100 and b < 180):
            dna["family_confidence"] = min(dna["family_confidence"], 0.5)
            for alt in ("Laminate", "Wood", "Veneer"):
                if alt.lower() != fam_l and alt not in dna["family_alternatives"]:
                    dna["family_alternatives"].append(alt)
            dna["family_alternatives"] = dna["family_alternatives"][:3]

    if not dna["canonical_description"]:
        dna["canonical_description"] = build_canonical_text(dna)
    return dna


def build_canonical_text(dna: dict) -> str:
    """Compose the text that gets embedded. Deterministic, same recipe on
    catalogue and query side — this is what makes the two domains comparable.

    Sprint 8.2 note: `material_family` (Paint / Tile / Stone / Laminate / …)
    is ALWAYS appended when present so the embedding always has the family
    keyword to anchor on, even when `surface_type` is a generic phrase like
    "smooth white surface". Empirically, without this the query embedding
    for a plain painted ceiling was too dissimilar from catalogue paint
    records (which explicitly say "emulsion" / "paint"), and retrieval
    dropped every paint candidate below the min_overall gate."""
    bits = []
    pc = dna.get("primary_color") or {}
    if pc.get("name"):
        bits.append(pc["name"])
    fam = dna.get("material_family")
    st = dna.get("surface_type")
    if st:
        bits.append(st)
    if fam:
        fam_l = fam.lower()
        already = " ".join(bits).lower()
        if fam_l not in already:
            bits.append(fam_l)
    if dna.get("pattern") and dna["pattern"].lower() not in ("plain solid", "none", "plain"):
        bits.append(f"with {dna['pattern']}")
    if dna.get("texture"):
        bits.append(f"{dna['texture']} texture")
    if dna.get("finish"):
        bits.append(f"{dna['finish']} finish")
    if dna.get("gloss_level"):
        bits.append(f"{dna['gloss_level']} gloss")
    apps = dna.get("typical_applications") or []
    if apps:
        bits.append("used for " + ", ".join(apps[:3]))
    return ", ".join(b for b in bits if b) or "unidentified material"


def embedding_text(dna: dict) -> str:
    """The final string handed to the embedder: canonical description plus
    the attribute recipe, so both free-form and structured signals land in
    the vector."""
    desc = dna.get("canonical_description") or ""
    recipe = build_canonical_text(dna)
    if desc and desc.lower() != recipe.lower():
        return f"{desc} {recipe}"
    return recipe or desc


def dna_from_record(rec: dict) -> dict:
    """Metadata-only DNA for catalogue records that have no swatch image
    (e.g. seeded library rows with rich text metadata)."""
    keywords = rec.get("keywords") or []
    pattern = _clean_str(rec.get("pattern"))
    return normalize_dna({
        "material_family": rec.get("material_family") or rec.get("category"),
        "surface_type": _clean_str(rec.get("material_name")),
        "primary_color": {"name": _clean_str(rec.get("color_name")), "hex": _clean_str(rec.get("color_hex"))},
        "texture": _clean_str(rec.get("texture")),
        "pattern": pattern,
        "finish": _clean_str(rec.get("finish")),
        "gloss_level": _clean_str(rec.get("gloss_level")),
        "typical_applications": [],
        "canonical_description": " ".join(filter(None, [
            _clean_str(rec.get("color_name")),
            _clean_str(rec.get("material_name")),
            _clean_str(rec.get("material_family") or rec.get("category")),
            _clean_str(rec.get("texture")),
            _clean_str(rec.get("finish")),
            " ".join(keywords[:6]),
        ])),
    })


def dna_from_query_row(row: dict) -> dict:
    """Query-side DNA built from an analyze/analyze-region LLM row."""
    keywords = row.get("keywords") or []
    return normalize_dna({
        "material_family": row.get("material_family"),
        "family_confidence": row.get("family_confidence", 1.0),
        "family_alternatives": row.get("family_alternatives") or [],
        "surface_type": _clean_str(row.get("material_type")),
        "primary_color": {"name": _clean_str(row.get("color")), "hex": _clean_str(row.get("color_hex"))},
        "texture": _clean_str(row.get("texture")),
        "pattern": _clean_str(row.get("pattern")),
        "finish": _clean_str(row.get("finish")),
        "gloss_level": _clean_str(row.get("gloss_level")),
        "typical_applications": [a for a in [_clean_str(row.get("object_type") or row.get("zone"))] if a],
        "canonical_description": " ".join(filter(None, [
            _clean_str(row.get("color")),
            _clean_str(row.get("material_type")),
            _clean_str(row.get("texture")),
            _clean_str(row.get("finish")),
            "on " + _clean_str(row.get("object_type") or row.get("zone")) if (row.get("object_type") or row.get("zone")) else "",
            " ".join(str(k) for k in keywords[:6]),
        ])),
    })


def parse_dna_json(raw: str) -> dict | None:
    """Robustly parse the vision model's JSON reply into normalized DNA."""
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return normalize_dna(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return normalize_dna(json.loads(m.group(0)))
            except (json.JSONDecodeError, TypeError):
                pass
    logger.warning("visual_dna: unparseable vision reply: %r", raw[:150])
    return None


async def generate_swatch_dna(swatch_b64: str, metadata: dict, api_key: str,
                              provider: str, model: str, timeout_s: int = 45) -> dict | None:
    """One vision call on an isolated swatch crop -> Visual DNA dict.
    Returns None on any failure — callers fall back to dna_from_record."""
    import asyncio
    import secrets
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    meta_lines = "\n".join(
        f"- {k}: {v}" for k, v in metadata.items() if v not in (None, "", [])
    ) or "- (none)"
    try:
        chat = LlmChat(
            api_key=api_key,
            session_id=f"dna-{secrets.token_hex(4)}",
            system_message=SWATCH_DNA_SYSTEM,
        ).with_model(provider, model).with_params(temperature=0)
        msg = UserMessage(
            text=SWATCH_DNA_PROMPT.format(metadata=meta_lines),
            file_contents=[ImageContent(image_base64=swatch_b64)],
        )
        raw = await asyncio.wait_for(chat.send_message(msg), timeout=timeout_s)
        return parse_dna_json(raw)
    except Exception as e:
        logger.warning("visual_dna: generation failed (%s: %s)", type(e).__name__, e)
        return None
