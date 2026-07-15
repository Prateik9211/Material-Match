"""Confidence calibration + match explanations.

Two confidence regimes, clearly separated:
  - retrieval-only  -> capped at RETRIEVAL_CONF_CAP (embeddings alone never
                       claim visual certainty)
  - visually re-ranked -> the re-rank score IS the confidence
  - pHash exact loopback -> 100 (pixel-level identity)
"""
from __future__ import annotations

RETRIEVAL_CONF_CAP = 88
EXACT_LOOPBACK_CONF = 100


def retrieval_confidence(retrieval_score: float, embedding_similarity: float | None) -> int:
    """Map hybrid score (0..1) to 0..100. BGE cosine for unrelated texts sits
    ~0.55-0.65, strong matches ~0.85+, so the useful band is remapped."""
    s = max(0.0, min(1.0, retrieval_score))
    conf = int(round(100 * (s - 0.45) / 0.50))
    if embedding_similarity is None:
        conf = min(conf, 75)  # attribute-only evidence is weaker
    return max(0, min(RETRIEVAL_CONF_CAP, conf))


def reranked_confidence(rerank_score: int) -> int:
    return max(0, min(100, int(rerank_score)))


def compose_retrieval_reason(candidate: dict, query_dna: dict) -> str:
    """Explanation for a retrieval-only match — cites concrete attribute
    evidence, never fabricates visual certainty."""
    attr = candidate["attribute_similarity"]
    cdna = candidate["item"].get("visual_dna") or {}
    bits = []
    cname = (cdna.get("primary_color") or {}).get("name")
    if attr["color"] >= 0.75 and cname:
        bits.append(f"{cname.lower()} tone")
    if attr["texture"] >= 0.5 and cdna.get("texture"):
        bits.append(f"{cdna['texture'].lower()}")
    if attr["finish"] >= 0.5 and cdna.get("finish"):
        bits.append(f"{cdna['finish'].lower()} finish")
    if attr["family"] >= 1.0 and cdna.get("material_family"):
        bits.append(f"same family ({cdna['material_family'].lower()})")
    emb = candidate.get("embedding_similarity")
    basis = f"semantic similarity {int(round(emb * 100))}%" if emb is not None else "attribute similarity"
    if bits:
        return f"Matched on {', '.join(bits[:3])} — {basis}. Not yet visually verified."
    return f"Matched on {basis}. Not yet visually verified."
