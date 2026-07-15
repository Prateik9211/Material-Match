"""Sprint 7 — Describe-Embed-Rerank intelligence layer tests.

Covers each modular component in isolation plus the composed pipeline:
  • Visual DNA normalisation + canonical text (dna.py)
  • Embedding provider + cosine sanity (embeddings.py)
  • Attribute similarity + hybrid retrieval (retrieval.py)
  • Confidence calibration boundaries (confidence.py)
  • Re-rank JSON parsing robustness (rerank.py — no LLM call)
  • Pipeline: exact pHash loopback wins, retrieval cap, DNA-less records
    are invisible to the matcher (honest by design)
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app/backend")


# ────────────────────────────────────────────────────────────────────────
# dna.py
# ────────────────────────────────────────────────────────────────────────

class TestVisualDNA:
    def test_normalize_fills_all_keys(self):
        from intelligence.dna import normalize_dna, DNA_KEYS
        dna = normalize_dna({"material_family": "laminate"})
        for k in DNA_KEYS:
            assert k in dna, f"missing {k}"
        assert dna["material_family"] == "Laminate"
        assert dna["dna_version"] == 1

    def test_canonical_text_composes_attributes(self):
        from intelligence.dna import normalize_dna, build_canonical_text
        dna = normalize_dna({
            "material_family": "Laminate",
            "surface_type": "wood-grain decorative laminate",
            "primary_color": {"name": "walnut brown", "hex": "#5C4033"},
            "texture": "straight grain",
            "finish": "matte",
            "gloss_level": "low",
            "typical_applications": ["kitchen cabinet front"],
        })
        text = build_canonical_text(dna)
        for token in ("walnut brown", "wood-grain", "straight grain", "matte", "kitchen cabinet"):
            assert token in text, f"'{token}' missing from: {text}"

    def test_dna_from_record_uses_metadata(self):
        from intelligence.dna import dna_from_record
        rec = {"material_family": "Wood", "material_name": "European Warm Oak",
               "color_name": "Warm honey", "color_hex": "#B98A5A",
               "texture": "Vertical straight grain", "finish": "Matte Oiled",
               "keywords": ["oak", "warm"]}
        dna = dna_from_record(rec)
        assert dna["material_family"] == "Wood"
        assert dna["primary_color"]["hex"] == "#B98A5A"
        assert "oak" in dna["canonical_description"].lower()

    def test_parse_dna_json_strips_markdown_fences(self):
        from intelligence.dna import parse_dna_json
        raw = '```json\n{"material_family": "Tile", "finish": "gloss"}\n```'
        dna = parse_dna_json(raw)
        assert dna and dna["material_family"] == "Tile"

    def test_parse_dna_json_garbage_returns_none(self):
        from intelligence.dna import parse_dna_json
        assert parse_dna_json("not json at all") is None
        assert parse_dna_json("") is None


# ────────────────────────────────────────────────────────────────────────
# embeddings.py
# ────────────────────────────────────────────────────────────────────────

class TestEmbeddings:
    def test_same_material_beats_different_material(self):
        from intelligence.embeddings import get_embedder, cosine
        e = get_embedder()
        a, b, c = e.embed([
            "warm walnut brown wood-grain laminate, matte finish",
            "walnut laminate with vertical woodgrain, low gloss",
            "glossy white marble slab with grey veining",
        ])
        assert cosine(a, b) > cosine(a, c), "embedding failed material discrimination"

    def test_cosine_edge_cases(self):
        from intelligence.embeddings import cosine
        assert cosine([], [1.0]) == 0.0
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
        assert abs(cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9


# ────────────────────────────────────────────────────────────────────────
# retrieval.py
# ────────────────────────────────────────────────────────────────────────

def _dna(**over):
    from intelligence.dna import normalize_dna
    base = {"material_family": "Laminate",
            "primary_color": {"name": "walnut brown", "hex": "#5C4033"},
            "texture": "straight grain", "finish": "matte"}
    base.update(over)
    return normalize_dna(base)


class TestAttributeSimilarity:
    def test_identical_dna_scores_high(self):
        from intelligence.retrieval import attribute_similarity
        d = _dna()
        s = attribute_similarity(d, d)
        assert s["overall"] > 0.85
        assert s["family"] == 1.0

    def test_wrong_family_penalised(self):
        from intelligence.retrieval import attribute_similarity
        s = attribute_similarity(_dna(material_family="Paint"),
                                 _dna(material_family="Tile"))
        assert s["family"] == 0.3

    def test_family_equivalence_laminate_wood(self):
        from intelligence.retrieval import attribute_similarity
        s = attribute_similarity(_dna(material_family="Wood"),
                                 _dna(material_family="Laminate"))
        assert s["family"] == 1.0

    def test_missing_data_is_neutral_not_zero(self):
        from intelligence.retrieval import attribute_similarity
        q = _dna(texture="", finish="", primary_color={"name": "", "hex": ""})
        s = attribute_similarity(q, _dna())
        assert s["texture"] == 0.5 and s["color"] == 0.5

    def test_close_colors_score_high(self):
        from intelligence.retrieval import attribute_similarity
        a = _dna(primary_color={"name": "walnut", "hex": "#5C4033"})
        b = _dna(primary_color={"name": "dark walnut", "hex": "#63452F"})
        c = _dna(primary_color={"name": "sky blue", "hex": "#87CEEB"})
        assert attribute_similarity(a, b)["color"] > attribute_similarity(a, c)["color"]


class TestRetrieve:
    def _items(self):
        from intelligence.dna import embedding_text
        from intelligence.embeddings import get_embedder
        e = get_embedder()
        dnas = [
            _dna(surface_type="walnut wood-grain laminate"),
            _dna(material_family="Tile", surface_type="white glossy ceramic tile",
                 primary_color={"name": "white", "hex": "#F5F5F5"}, finish="gloss"),
        ]
        vecs = e.embed([embedding_text(d) for d in dnas])
        return [
            {"id": "walnut", "visual_dna": dnas[0], "dna_embedding": vecs[0]},
            {"id": "tile", "visual_dna": dnas[1], "dna_embedding": vecs[1]},
        ]

    def test_correct_item_ranks_first(self):
        from intelligence.retrieval import retrieve
        from intelligence.dna import embedding_text
        from intelligence.embeddings import get_embedder
        q = _dna(surface_type="dark walnut laminate panel")
        qv = get_embedder().embed([embedding_text(q)])[0]
        out = retrieve(q, qv, self._items())
        assert out[0]["item"]["id"] == "walnut"
        assert out[0]["retrieval_score"] > out[1]["retrieval_score"]

    def test_items_without_dna_are_invisible(self):
        from intelligence.retrieval import retrieve
        out = retrieve(_dna(), [0.1] * 384, [{"id": "blind-record"}])
        assert out == []


# ────────────────────────────────────────────────────────────────────────
# confidence.py
# ────────────────────────────────────────────────────────────────────────

class TestConfidence:
    def test_retrieval_confidence_capped(self):
        from intelligence.confidence import retrieval_confidence, RETRIEVAL_CONF_CAP
        assert retrieval_confidence(1.0, 0.99) == RETRIEVAL_CONF_CAP
        assert retrieval_confidence(0.0, 0.1) == 0

    def test_attribute_only_evidence_capped_lower(self):
        from intelligence.confidence import retrieval_confidence
        assert retrieval_confidence(0.95, None) <= 75

    def test_reranked_confidence_owns_full_range(self):
        from intelligence.confidence import reranked_confidence
        assert reranked_confidence(97) == 97
        assert reranked_confidence(150) == 100
        assert reranked_confidence(-5) == 0


# ────────────────────────────────────────────────────────────────────────
# rerank.py — parser only (no LLM call)
# ────────────────────────────────────────────────────────────────────────

class TestRerankParser:
    def test_parses_clean_json(self):
        from intelligence.rerank import _parse_rerank
        raw = ('{"results": [{"candidate": 0, "score": 92, "verdict": "accept", '
               '"reason": "same walnut grain"}, {"candidate": 1, "score": 20, '
               '"verdict": "reject", "reason": "different colour"}]}')
        out = _parse_rerank(raw, 2)
        assert len(out) == 2
        assert out[0]["verdict"] == "accept" and out[1]["verdict"] == "reject"

    def test_parses_fenced_json(self):
        from intelligence.rerank import _parse_rerank
        raw = '```json\n{"results": [{"candidate": 0, "score": 80, "verdict": "accept", "reason": "x"}]}\n```'
        out = _parse_rerank(raw, 1)
        assert out and out[0]["score"] == 80

    def test_out_of_range_candidates_dropped(self):
        from intelligence.rerank import _parse_rerank
        raw = '{"results": [{"candidate": 9, "score": 80, "verdict": "accept", "reason": "x"}]}'
        assert _parse_rerank(raw, 2) is None

    def test_garbage_returns_none(self):
        from intelligence.rerank import _parse_rerank
        assert _parse_rerank("I cannot help with that", 3) is None


# ────────────────────────────────────────────────────────────────────────
# pipeline.py
# ────────────────────────────────────────────────────────────────────────

class TestPipeline:
    def _swatch_b64(self, color=(180, 138, 85)):
        import base64
        import io
        from PIL import Image
        img = Image.new("RGB", (240, 240), color)
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return base64.b64encode(buf.getvalue()).decode()

    def test_exact_loopback_wins_and_scores_100(self):
        from intelligence.pipeline import retrieve_matches
        from visual_hash import compute_visual_hashes
        b64 = self._swatch_b64()
        item = {"id": "aurum", "visual_hashes": compute_visual_hashes(b64),
                "visual_dna": _dna(), "dna_embedding": None}
        res = retrieve_matches(_dna(), compute_visual_hashes(b64), [item])
        assert res["candidates"], "exact loopback missed"
        top = res["candidates"][0]
        assert top["stage"] == "exact_loopback"
        assert top["confidence"] == 100
        assert top["exact_visual_match"] is True

    def test_no_exact_hit_falls_to_retrieval(self):
        from intelligence.dna import embedding_text
        from intelligence.embeddings import get_embedder
        from intelligence.pipeline import retrieve_matches
        d = _dna()
        vec = get_embedder().embed([embedding_text(d)])[0]
        item = {"id": "x", "visual_dna": d, "dna_embedding": vec}
        res = retrieve_matches(_dna(), None, [item])
        assert res["candidates"][0]["stage"] == "retrieval"
        assert res["candidates"][0]["confidence"] <= 88

    def test_empty_library_returns_empty(self):
        from intelligence.pipeline import retrieve_matches
        res = retrieve_matches(_dna(), None, [])
        assert res["candidates"] == []


# ────────────────────────────────────────────────────────────────────────
# server integration — matcher end-to-end (no LLM)
# ────────────────────────────────────────────────────────────────────────

class TestServerMatcherIntegration:
    def test_blue_cabinet_query_returns_laminates_not_paint(self):
        """The 'blue rectangle -> paint' regression guard, at the retrieval
        layer: a cabinet-front laminate query gated to Laminates must never
        surface a Paint record."""
        from server import _find_catalogue_matches
        row = {
            "zone": "Kitchen base cabinet front",
            "object_type": "kitchen cabinet",
            "material_family": "wood",
            "material_type": "matte blue laminate cabinet front",
            "color": "Deep blue",
            "texture": "smooth",
            "finish": "matte",
            "keywords": ["blue", "laminate", "cabinet", "kitchen"],
        }
        matches = _find_catalogue_matches(
            row, top_k=6, allowed_categories=["Laminates", "Veneers"],
            min_overall=40,
        )
        for m in matches:
            assert m["category"] not in ("Paints", "Paint"), (
                f"Paint record surfaced for cabinet query: {m['material_name']}"
            )

    def test_every_match_carries_sprint7_debug(self):
        from server import _find_catalogue_matches
        row = {"material_family": "Paint", "material_type": "warm white matte",
               "color": "warm white", "finish": "matte", "keywords": ["warm white"]}
        for m in _find_catalogue_matches(row, top_k=4):
            dbg = m["debug"]
            assert dbg["pipeline_stage"] in ("retrieval", "exact_loopback")
            assert "retrieval_score" in dbg
            assert "rerank_score" in dbg and "rerank_verdict" in dbg
            assert m["visually_verified"] in (True, False)
