"""Pipeline orchestrator — pHash exact -> retrieve -> (optional) rerank.

Sync `retrieve_matches` powers every zone instantly (lazy strategy);
async `apply_rerank` is invoked only for user-selected regions or on
demand. Each stage delegates to its own module so any single component
can be upgraded without touching the others.
"""
from __future__ import annotations

import logging

from .confidence import (EXACT_LOOPBACK_CONF, compose_retrieval_reason,
                         retrieval_confidence)
from .dna import embedding_text
from .embeddings import get_embedder
from .retrieval import retrieve

logger = logging.getLogger(__name__)

PHASH_EXACT_MAX = 6  # pixel-identity shortcut only — never a ranking signal


def _color_gap(a: dict, b: dict) -> float | None:
    """Weighted RGB distance between the two packets' average colours.
    None when either side lacks the Sprint 7 avg_rgb field."""
    ra, rb = a.get("avg_rgb"), b.get("avg_rgb")
    if not ra or not rb:
        return None
    dr, dg, db = ra[0] - rb[0], ra[1] - rb[1], ra[2] - rb[2]
    return (2 * dr * dr + 4 * dg * dg + 3 * db * db) ** 0.5


EXACT_COLOR_GAP_MAX = 40.0  # near-identical images agree on mean colour


def _exact_loopback(ref_hashes: dict | None, items: list[dict]) -> list[dict]:
    if not ref_hashes:
        return []
    from visual_hash import hamming
    hits = []
    for item in items:
        ih = item.get("visual_hashes")
        if not ih:
            continue
        # pHash ONLY — dhash/whash collapse to 0 on flat, gradient-free
        # swatches, which made visually different colours look "exact".
        d = hamming(ref_hashes.get("phash"), ih.get("phash"))
        if d > PHASH_EXACT_MAX:
            continue
        gap = _color_gap(ref_hashes, ih)
        if gap is not None and gap > EXACT_COLOR_GAP_MAX:
            continue  # structurally similar but a different colour — not exact
        hits.append({"item": item, "hamming": d})
    hits.sort(key=lambda h: h["hamming"])
    return hits


def retrieve_matches(query_dna: dict, ref_hashes: dict | None,
                     items: list[dict], top_k: int = 8) -> dict:
    """Stage 2-4 of the pipeline (hard category filtering is done by the
    caller's Brain gate before `items` arrives here).

    Returns {"candidates": [...], "meta": {...}} where each candidate is
    {item, confidence, reason, stage, embedding_similarity,
    attribute_similarity, retrieval_score, exact_visual_match, hamming}."""
    meta = {"embedder": None, "exact_loopback_hits": 0, "indexed_items": len(items)}
    out = []

    exact = _exact_loopback(ref_hashes, items)
    exact_ids = set()
    for h in exact[:top_k]:
        exact_ids.add(id(h["item"]))
        out.append({
            "item": h["item"],
            "confidence": EXACT_LOOPBACK_CONF,
            "reason": (f"Exact visual match with the published catalogue swatch "
                       f"(pixel-level identity, Hamming {h['hamming']})."),
            "stage": "exact_loopback",
            "embedding_similarity": None,
            "attribute_similarity": None,
            "retrieval_score": 1.0,
            "exact_visual_match": True,
            "hamming": h["hamming"],
        })
    meta["exact_loopback_hits"] = len(out)

    try:
        embedder = get_embedder()
        query_vec = embedder.embed([embedding_text(query_dna)])[0]
        meta["embedder"] = embedder.name
    except Exception as e:
        logger.warning("pipeline: embedding failed (%s) — attribute-only retrieval", e)
        query_vec = None

    for cand in retrieve(query_dna, query_vec, items, top_k=top_k):
        if id(cand["item"]) in exact_ids:
            continue
        conf = retrieval_confidence(cand["retrieval_score"], cand["embedding_similarity"])
        out.append({
            **cand,
            "confidence": conf,
            "reason": compose_retrieval_reason(cand, query_dna),
            "stage": "retrieval",
            "exact_visual_match": False,
            "hamming": None,
        })
        if len(out) >= top_k:
            break
    return {"candidates": out, "meta": meta}
