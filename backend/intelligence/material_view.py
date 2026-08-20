"""Nano Banana (gemini-3.1-flash-image-preview) — photorealistic
'Material View' rendering from a flat catalogue swatch.

Kept as a thin, blocking-safe helper so callers can either await it
directly (bulk backfill loop) or schedule it via `asyncio.create_task`
from a publish handler.

Design decisions locked from the 2026-02-14 feasibility test:
  * Model: `gemini-3.1-flash-image-preview` (Nano Banana). GPT-Image-1
    hallucinated on marble in feasibility — we do NOT use it.
  * Image-to-image ONLY: the real swatch is sent as reference so the
    generated view preserves the material's exact colour, grain and
    pattern. No text-only fallback.
  * Grounding prompt is assembled from the record's DNA fields
    (material_family, material_name, color hex, finish, texture,
    pattern) so the generator has both the pixels AND the semantics.
"""
from __future__ import annotations
import asyncio
import base64
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _dna_prompt(rec: dict) -> str:
    """Build a Nano-Banana-friendly grounding prompt from a ke_records row."""
    family = (rec.get("material_family") or "material").strip()
    name = (rec.get("material_name") or rec.get("color_name") or family).strip()
    finish = (rec.get("finish") or "").strip()
    texture = (rec.get("texture") or "").strip()
    pattern = (rec.get("pattern") or "").strip()
    color_name = (rec.get("color_name") or "").strip()
    color_hex = (rec.get("color_hex") or "").strip()

    parts = [
        f"Using the attached flat catalogue swatch as the SOURCE material, "
        f"produce a photorealistic 'material view' — a close-up render of "
        f"this exact {family} ({name}) as a physical surface panel under "
        f"soft, even architectural studio lighting.",
        "Preserve the swatch's colour, grain direction, pattern and finish exactly.",
    ]
    if color_name or color_hex:
        parts.append(f"Colour: {color_name} (hex {color_hex}).")
    if finish:
        parts.append(f"Finish: {finish}.")
    if texture:
        parts.append(f"Texture: {texture}.")
    if pattern:
        parts.append(f"Pattern: {pattern}.")
    parts.append(
        "Show correct micro-texture and material reflectivity as it would "
        "look physically installed. Neutral background. No labels, no props, "
        "no text, no watermark."
    )
    return " ".join(parts)


async def generate_material_view(rec: dict, swatch_b64: str,
                                  timeout_s: int = 60) -> Optional[bytes]:
    """Return raw PNG bytes of the generated Material View, or None on failure.

    We swallow generator errors here and return None so a batch backfill
    can continue past individual failures. Callers log the record ID.
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        logger.warning("material_view: EMERGENT_LLM_KEY not set — skipping")
        return None
    if not swatch_b64:
        return None

    # Imported inside the function so worker startup doesn't pay the cost
    # for records that never trigger generation.
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

    prompt = _dna_prompt(rec)
    session_id = f"matview-{rec.get('id') or 'anon'}"

    try:
        chat = LlmChat(
            api_key=api_key,
            session_id=session_id,
            system_message="You are a material visualization assistant.",
        )
        chat.with_model("gemini", "gemini-3.1-flash-image-preview") \
            .with_params(modalities=["image", "text"])
        msg = UserMessage(text=prompt, file_contents=[ImageContent(swatch_b64)])
        text, images = await asyncio.wait_for(
            chat.send_message_multimodal_response(msg),
            timeout=timeout_s,
        )
    except Exception as e:
        logger.warning("material_view: generation failed for %s: %s",
                       rec.get("id"), str(e)[:200])
        return None

    if not images:
        logger.warning("material_view: no image returned for %s (text=%r)",
                       rec.get("id"), (text or "")[:120])
        return None

    try:
        return base64.b64decode(images[0]["data"])
    except Exception as e:
        logger.warning("material_view: decode failed for %s: %s",
                       rec.get("id"), e)
        return None
