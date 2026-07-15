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

SWATCH_DNA_PROMPT = """Known metadata (may be incomplete or wrong — trust the IMAGE over the text):
{metadata}

Return exactly this JSON:
{{
  "material_family": "one of: Laminate | Veneer | Tile | Paint | Stone | Fabric | Wood | Metal | Wallpaper | Other",
  "surface_type": "specific surface e.g. 'wood-grain decorative laminate', 'polished marble slab'",
  "primary_color": {{"name": "short colour name", "hex": "#RRGGBB"}},
  "secondary_colors": [{{"name": "...", "hex": "#RRGGBB"}}],
  "color_temperature": "warm | cool | neutral",
  "texture": "short texture description e.g. 'straight grain, medium contrast'",
  "pattern": "short pattern description e.g. 'linear woodgrain, vertical' or 'plain solid'",
  "pattern_scale": "fine | medium | large | none",
  "finish": "matte | satin | gloss | textured | brushed | polished",
  "gloss_level": "low | medium | high",
  "typical_applications": ["2-4 surfaces this is typically used on, e.g. 'kitchen cabinet front', 'feature wall'"],
  "canonical_description": "ONE sentence, <=40 words, describing colour, pattern, texture and finish as a designer would."
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
    dna = {
        "material_family": _clean_str(raw.get("material_family")).title(),
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
    if not dna["canonical_description"]:
        dna["canonical_description"] = build_canonical_text(dna)
    return dna


def build_canonical_text(dna: dict) -> str:
    """Compose the text that gets embedded. Deterministic, same recipe on
    catalogue and query side — this is what makes the two domains comparable."""
    bits = []
    pc = dna.get("primary_color") or {}
    if pc.get("name"):
        bits.append(pc["name"])
    if dna.get("surface_type"):
        bits.append(dna["surface_type"])
    elif dna.get("material_family"):
        bits.append(dna["material_family"].lower())
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
        ).with_model(provider, model)
        msg = UserMessage(
            text=SWATCH_DNA_PROMPT.format(metadata=meta_lines),
            file_contents=[ImageContent(image_base64=swatch_b64)],
        )
        raw = await asyncio.wait_for(chat.send_message(msg), timeout=timeout_s)
        return parse_dna_json(raw)
    except Exception as e:
        logger.warning("visual_dna: generation failed (%s: %s)", type(e).__name__, e)
        return None
