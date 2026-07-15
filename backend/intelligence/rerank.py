"""Visual re-rank — the only stage that compares actual pixels.

One LLM call: the user's selected crop + up to RERANK_MAX_CANDIDATES
candidate swatch images. The model scores each candidate 0-100 and issues
an accept/reject verdict with a reason. Model is env-configurable
(RERANK_PROVIDER / RERANK_MODEL) — the pipeline never hardcodes GPT-4o.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets

logger = logging.getLogger(__name__)

RERANK_PROVIDER = os.environ.get("RERANK_PROVIDER", "openai")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "gpt-4o")
RERANK_MAX_CANDIDATES = int(os.environ.get("RERANK_MAX_CANDIDATES", "8"))
RERANK_TIMEOUT_S = int(os.environ.get("RERANK_TIMEOUT_S", "60"))

RERANK_SYSTEM = (
    "You are a materials matching expert for architects. IMAGE 1 is a crop "
    "from a real interior photograph showing a material surface the designer "
    "wants to source. The remaining images are isolated supplier catalogue "
    "swatches. Judge, per swatch, whether it is plausibly the SAME product "
    "as the surface in IMAGE 1. Account for perspective, lighting, shadows "
    "and gloss reflections in the photograph — a swatch can match even if "
    "the photo looks darker or warmer. Account also for RESOLUTION SCALE: "
    "the crop is usually zoomed-out and the swatch is a close-up — a subtle "
    "beige texture in the crop and a distinct beige woven weave in the swatch "
    "may well be the same material at different distances. If the crop shows "
    "more than one material (e.g. a floor plus a bed leg, or a wall plus a "
    "wooden trim), judge each candidate against the DOMINANT surface in the "
    "crop — don't reject just because other objects are also visible. Judge "
    "colour, grain/pattern nature (linear-wood vs woven vs veined), texture "
    "and finish. Be strict on family incompatibility ('similar vibe' across "
    "families is NOT a match), but be permissive on scale-of-detail within "
    "the same family. Reply with ONLY valid JSON, no markdown."
)


def _rerank_prompt(query_context: str, candidates: list[dict], image_map: list[int]) -> str:
    lines = []
    for pos, ci in enumerate(image_map):
        c = candidates[ci]
        item = c["item"]
        dna = item.get("visual_dna") or {}
        lines.append(
            f"IMAGE {pos + 2} = candidate {ci}: {item.get('material_name')} "
            f"({item.get('brand')}, {item.get('category')}) — "
            f"{dna.get('canonical_description') or ''}"
        )
    no_img = [i for i in range(len(candidates)) if i not in image_map]
    for ci in no_img:
        item = candidates[ci]["item"]
        dna = item.get("visual_dna") or {}
        lines.append(
            f"candidate {ci} (NO IMAGE, judge on description only): "
            f"{item.get('material_name')} ({item.get('brand')}) — "
            f"{dna.get('canonical_description') or ''}"
        )
    return (
        f"The designer selected this surface (AUTHORITATIVE description "
        f"of the target material — TRUST this identity when the photograph "
        f"shows additional context around the material): {query_context}\n\n"
        + "\n".join(lines)
        + "\n\nReturn JSON:\n"
        '{"results": [{"candidate": <index>, "score": 0-100, '
        '"verdict": "accept" | "reject", '
        '"reason": "<=25 words citing concrete visual evidence"}]}\n'
        "RULES:\n"
        "- score >= 75 means you believe it is the same or a near-identical product.\n"
        "- verdict=accept ONLY when score >= 60 AND the material type is compatible.\n"
        "- Match each candidate against the DESCRIBED surface, NOT the entire scene. "
        "Ignore background walls, adjacent objects, floor edges, ceiling paint and other "
        "materials that appear alongside the target surface in the photograph.\n"
        "- If NO candidate matches, reject all of them. Never force a match.\n"
        "- Include EVERY candidate index exactly once."
    )


def _parse_rerank(raw: str, n: int) -> list[dict] | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except (json.JSONDecodeError, TypeError):
            return None
    results = data.get("results") if isinstance(data, dict) else data
    if not isinstance(results, list):
        return None
    out = []
    for r in results:
        try:
            idx = int(r.get("candidate"))
            if not (0 <= idx < n):
                continue
            out.append({
                "candidate": idx,
                "score": max(0, min(100, int(r.get("score", 0)))),
                "verdict": "accept" if str(r.get("verdict", "")).lower() == "accept" else "reject",
                "reason": str(r.get("reason") or "")[:220],
            })
        except (TypeError, ValueError):
            continue
    return out or None


async def visual_rerank(crop_b64: str, candidates: list[dict], query_context: str,
                        api_key: str) -> list[dict] | None:
    """Re-rank `candidates` (retrieval output dicts) against the user crop.
    Returns [{candidate, score, verdict, reason}] or None if the call
    failed — callers keep retrieval-only results on None (fail open)."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    if not candidates or not crop_b64 or not api_key:
        return None
    candidates = candidates[:RERANK_MAX_CANDIDATES]
    images = [ImageContent(image_base64=crop_b64)]
    image_map: list[int] = []
    for i, c in enumerate(candidates):
        swatch = c["item"].get("swatch_crop_b64") or c["item"].get("page_preview_b64")
        if swatch:
            if swatch.startswith("data:"):
                swatch = swatch.split(",", 1)[-1]
            images.append(ImageContent(image_base64=swatch))
            image_map.append(i)
    try:
        chat = LlmChat(
            api_key=api_key,
            session_id=f"rerank-{secrets.token_hex(4)}",
            system_message=RERANK_SYSTEM,
        ).with_model(RERANK_PROVIDER, RERANK_MODEL).with_params(temperature=0)
        msg = UserMessage(
            text=_rerank_prompt(query_context, candidates, image_map),
            file_contents=images,
        )
        raw = await asyncio.wait_for(chat.send_message(msg), timeout=RERANK_TIMEOUT_S)
        parsed = _parse_rerank(raw, len(candidates))
        if parsed:
            logger.info("rerank: model=%s candidates=%d accepted=%d",
                        RERANK_MODEL, len(candidates),
                        sum(1 for p in parsed if p["verdict"] == "accept"))
            # Sprint 8 — debug: log each verdict + score + reason so we can
            # trace why real-photo rerank rejects visually-obvious matches.
            for p in parsed:
                c = candidates[p["candidate"]]
                it = c["item"]
                logger.info(
                    "rerank verdict: %s score=%d name=%r brand=%r reason=%r",
                    p["verdict"], p["score"],
                    it.get("material_name"), it.get("brand"), p["reason"],
                )
        return parsed
    except Exception as e:
        logger.warning("rerank: failed (%s: %s) — keeping retrieval order",
                       type(e).__name__, e)
        return None
