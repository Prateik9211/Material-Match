"""Candidate generation — hard filters + hybrid vector/attribute scoring.

Model-agnostic: consumes pre-computed embedding vectors, never calls an
LLM. Attribute similarity is a deterministic complement to the embedding
so a hex-perfect colour match or exact family agreement still counts when
descriptions are phrased differently.
"""
from __future__ import annotations

from .embeddings import cosine

EMBED_WEIGHT = 0.65
ATTR_WEIGHT = 0.35

_FAMILY_EQUIV = {
    "laminate": {"laminate", "laminates", "wood", "veneer"},
    "veneer": {"veneer", "veneers", "wood", "laminate"},
    "wood": {"wood", "laminate", "veneer", "flooring", "furniture"},
    "tile": {"tile", "tiles", "stone", "flooring"},
    "stone": {"stone", "tile", "tiles", "flooring"},
    "paint": {"paint", "paints", "wall"},
    "wall": {"wall", "paint", "paints", "wallpaper"},
    "fabric": {"fabric", "textile", "upholstery"},
    "textile": {"textile", "fabric", "upholstery"},
    "upholstery": {"upholstery", "fabric", "textile"},
    "metal": {"metal", "hardware"},
    "flooring": {"flooring", "wood", "tile", "tiles", "stone"},
    "furniture": {"furniture", "wood", "laminate", "veneer"},
    "wallpaper": {"wallpaper", "wall"},
}


def _tokens(s: str) -> set:
    return {t for t in str(s or "").lower().replace(",", " ").replace("-", " ").split() if len(t) > 2}


def _hex_to_rgb(h: str):
    h = (h or "").lstrip("#")
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _rgb_to_hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Cheap RGB→HSV conversion (H in degrees 0..360, S/V in 0..1)."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    diff = mx - mn
    if diff == 0:
        h = 0.0
    elif mx == rf:
        h = (60 * ((gf - bf) / diff) + 360) % 360
    elif mx == gf:
        h = (60 * ((bf - rf) / diff) + 120) % 360
    else:
        h = (60 * ((rf - gf) / diff) + 240) % 360
    s = 0.0 if mx == 0 else diff / mx
    return h, s, mx


def _color_sim(hex_a: str, hex_b: str) -> float | None:
    """Perceptual-ish colour similarity that respects HUE.

    Prior Sprint 7/8 implementation used weighted-RGB Euclidean distance —
    that treats a warm beige (#F1DDC3) as very close to a neutral light
    grey (#D3D3D3) because both channels are high, which broke retrieval
    for "cool grey oak floor" queries (warm-beige laminates outranked cool
    grey ones). We now also penalise HUE and SATURATION differences so
    beige vs grey no longer collapse.
    """
    ra, rb = _hex_to_rgb(hex_a), _hex_to_rgb(hex_b)
    if ra is None or rb is None:
        return None
    dr, dg, db = ra[0] - rb[0], ra[1] - rb[1], ra[2] - rb[2]
    # 1) Weighted RGB distance (lightness / overall closeness).
    rgb_dist = (2 * dr * dr + 4 * dg * dg + 3 * db * db) ** 0.5  # max ~765
    rgb_sim = max(0.0, 1.0 - rgb_dist / 500.0)
    # 2) Hue + saturation penalty. Hue only matters when BOTH colours are
    #    at least mildly saturated — for two near-greys hue is meaningless.
    ha, sa, _ = _rgb_to_hsv(*ra)
    hb, sb, _ = _rgb_to_hsv(*rb)
    sat_min = min(sa, sb)
    # Circular hue distance in degrees, normalised 0..1.
    dh = min(abs(ha - hb), 360 - abs(ha - hb)) / 180.0
    ds = abs(sa - sb)
    # When one colour is essentially achromatic (sat < 0.10) and the other
    # is saturated (>= 0.20), that's a clear warm/cool mismatch — force
    # a hard penalty regardless of RGB distance.
    if (sa < 0.10 and sb >= 0.20) or (sb < 0.10 and sa >= 0.20):
        hue_sim = 0.35
    elif sat_min < 0.06:
        hue_sim = 1.0 - ds     # both near-grey — only saturation matters
    else:
        hue_sim = max(0.0, 1.0 - (0.7 * dh + 0.3 * ds))
    # Blend — RGB carries lightness, hue carries chromatic identity.
    return max(0.0, min(1.0, 0.6 * rgb_sim + 0.4 * hue_sim))


def _token_sim(a: str, b: str) -> float | None:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return None
    return len(ta & tb) / len(ta | tb)


def attribute_similarity(qdna: dict, cdna: dict) -> dict:
    """Deterministic field-wise similarity between two Visual DNA dicts.
    Missing data scores neutral (0.5) — absence of evidence is not
    evidence of mismatch. Returns per-field 0..1 plus weighted overall.

    2026-07 — when the query's DNA was flagged as low-confidence on
    family (`family_confidence < 0.7` from a plain / texture-less crop),
    any family in `family_alternatives` also scores as a full match.
    Prevents "flat cabinet door classified as Paint" from locking
    retrieval into the Paint catalogue and missing the correct Laminate
    entry — instead both catalogues are treated equally against the
    same colour / finish / pattern signals."""
    qfam = str(qdna.get("material_family") or "").lower()
    cfam = str(cdna.get("material_family") or "").lower()
    q_alts = [str(a).lower() for a in (qdna.get("family_alternatives") or [])]
    q_conf = float(qdna.get("family_confidence") or 1.0)
    if qfam and cfam:
        equiv = _FAMILY_EQUIV.get(qfam, {qfam})
        if cfam in equiv:
            family = 1.0
        elif q_conf < 0.7 and cfam in q_alts:
            # 2026-02-27 (round 7) — downgraded from 1.0 to 0.7.
            # Treating an alt-family as full-match let cross-category
            # results (laminate vs paint) score identically to same-
            # family results on high BGE similarity, producing 88%
            # laminate matches for confidently-classified matte wall
            # paints.  0.7 keeps alt families in the ranking without
            # putting them on equal footing with the actual family.
            family = 0.7
        else:
            family = 0.3
    else:
        family = 0.6

    qhex = (qdna.get("primary_color") or {}).get("hex")
    chex = (cdna.get("primary_color") or {}).get("hex")
    color = _color_sim(qhex, chex)
    if color is None:
        color = _token_sim((qdna.get("primary_color") or {}).get("name"),
                           (cdna.get("primary_color") or {}).get("name"))
    if color is None:
        color = 0.5

    texture = _token_sim(qdna.get("texture"), cdna.get("texture"))
    texture = 0.5 if texture is None else texture
    pattern = _token_sim(qdna.get("pattern"), cdna.get("pattern"))
    pattern = 0.5 if pattern is None else pattern
    finish = _token_sim(qdna.get("finish"), cdna.get("finish"))
    if finish is None:
        finish = 0.5
    qg, cg = qdna.get("gloss_level"), cdna.get("gloss_level")
    if qg and cg:
        finish = (finish + (1.0 if qg == cg else 0.3)) / 2

    overall = (0.25 * family + 0.35 * color + 0.15 * texture
               + 0.10 * pattern + 0.15 * finish)
    return {"family": family, "color": color, "texture": texture,
            "pattern": pattern, "finish": finish, "overall": overall}


def retrieve(query_dna: dict, query_vec: list[float], items: list[dict],
             top_k: int = 8) -> list[dict]:
    """Score every item carrying `visual_dna` (+ optional `dna_embedding`)
    against the query. Returns top_k candidates sorted by hybrid score.

    Each result: {item, embedding_similarity, attribute_similarity (dict),
    retrieval_score}. Items without DNA are skipped — they are invisible to
    the matcher until enriched (honest by design)."""
    scored = []
    for item in items:
        cdna = item.get("visual_dna")
        if not cdna:
            continue
        attr = attribute_similarity(query_dna, cdna)
        vec = item.get("dna_embedding")
        if query_vec and vec:
            emb = cosine(query_vec, vec)
            score = EMBED_WEIGHT * emb + ATTR_WEIGHT * attr["overall"]
        else:
            emb = None
            score = attr["overall"]
        scored.append({
            "item": item,
            "embedding_similarity": emb,
            "attribute_similarity": attr,
            "retrieval_score": score,
        })
    scored.sort(key=lambda c: c["retrieval_score"], reverse=True)
    return scored[:top_k]
