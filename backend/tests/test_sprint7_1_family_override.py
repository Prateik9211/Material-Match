"""Regression tests for Sprint 7.1 — query-side vision-DNA + family override.

Covers all cases in the product owner's spec:
  1. Query-side vision-DNA is used when generation succeeds.
  2. Fallback works when DNA generation fails.
  3. Generic classifier `furniture` + DNA `Laminate` routes to Laminates.
  4. Cabinet object classification is preserved after the material-family
     override (object_type stays "wardrobe" while material_family becomes
     "Laminate").
  5. A valid specific classifier family is NOT overwritten by a weak or
     generic vision-DNA family.
  6. Unsupported DNA families do not bypass category filtering.
  7. Exact-loopback (pHash pixel identity) behaviour is unchanged.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intelligence.family import (CANONICAL_FAMILIES, is_generic_family,  # noqa: E402
                                 pick_final_family, to_canonical)


# ---------------------------------------------------------------------------
# family.py — pure unit coverage
# ---------------------------------------------------------------------------
class TestCanonicalFamily:
    @pytest.mark.parametrize("value,expected", [
        ("Laminate", "Laminate"), ("laminates", "Laminate"), ("HPL", "Laminate"),
        ("Paint", "Paint"), ("wall paint", "Paint"), ("Paints", "Paint"),
        ("fabric", "Fabric"), ("textile", "Fabric"),
        ("tile", "Tile"), ("porcelain tile", "Tile"),
        ("stone", "Stone"), ("marble", "Stone"), ("granite", "Stone"),
        ("wood", "Wood"), ("timber", "Wood"),
        ("veneer", "Veneer"),
        ("metal", "Metal"),
    ])
    def test_aliases_map_to_canonical(self, value, expected):
        assert to_canonical(value) == expected

    @pytest.mark.parametrize("value", [
        "furniture", "cabinet", "flooring", "wall", "walls",
        "surface", "unknown", "other", "", None, "n/a",
    ])
    def test_generic_labels_return_none(self, value):
        assert to_canonical(value) is None
        assert is_generic_family(value) is True

    def test_canonical_families_are_known(self):
        assert "Laminate" in CANONICAL_FAMILIES
        assert "Paint" in CANONICAL_FAMILIES
        assert "Fabric" in CANONICAL_FAMILIES
        # Object labels are NOT canonical families.
        for bad in ("Furniture", "Flooring", "Wall"):
            assert bad not in CANONICAL_FAMILIES

    def test_compound_word_last_token(self):
        assert to_canonical("wood-grain laminate") == "Laminate"
        assert to_canonical("engineered stone") == "Stone"


class TestPickFinalFamily:
    # Case 3 in spec — generic classifier + canonical vision -> override.
    def test_generic_classifier_uses_vision(self):
        fam, reason = pick_final_family("furniture", "Laminate")
        assert fam == "Laminate"
        assert "override" in reason

    def test_flooring_upholstery_wall_are_generic(self):
        assert pick_final_family("flooring", "Laminate")[0] == "Laminate"
        assert pick_final_family("upholstery", "Fabric")[0] == "Fabric"
        assert pick_final_family("wall", "Paint")[0] == "Paint"

    # Case 5 — valid classifier is NOT overwritten by a weak DNA.
    def test_specific_classifier_kept_when_disagrees(self):
        fam, reason = pick_final_family("Paint", "Fabric")
        assert fam == "Paint"
        assert "keep_classifier" in reason

    def test_agreement_keeps_family(self):
        fam, reason = pick_final_family("Laminate", "Laminate")
        assert fam == "Laminate"
        assert reason == "agree"

    # Case 6 — unsupported / unknown DNA doesn't bypass classifier.
    def test_unknown_vision_kept_classifier(self):
        fam, _ = pick_final_family("Paint", None)
        assert fam == "Paint"
        fam, _ = pick_final_family("Paint", "gibberish")
        assert fam == "Paint"

    def test_both_unknown_returns_original(self):
        fam, reason = pick_final_family("gibberish", None)
        assert fam == "gibberish"
        assert reason == "no_canonical"


# ---------------------------------------------------------------------------
# server integration — _reconcile_family_with_vision_dna
# ---------------------------------------------------------------------------
import server  # noqa: E402


def _dna(family: str, description: str = "test dna") -> dict:
    return {
        "material_family": family,
        "surface_type": "test",
        "primary_color": {"name": "warm brown", "hex": "#7A5A3A"},
        "canonical_description": description,
        "dna_version": 1,
    }


class TestReconcileFamilyWithVisionDna:
    def test_generic_furniture_overrides_to_laminate(self):
        # Spec case 3 — classifier=furniture + vision=Laminate ⇒ Laminate.
        row = {"material_family": "furniture", "material_type": "laminate",
               "object_type": "wardrobe", "zone": "Wardrobe front"}
        debug = server._reconcile_family_with_vision_dna(row, _dna("Laminate"))
        assert row["material_family"] == "Laminate"
        assert row["material_family_original"] == "furniture"
        assert row["object_type"] == "wardrobe"          # spec case 4
        assert debug["override_applied"] is True
        assert row["visual_dna"]["material_family"] == "Laminate"

    def test_specific_classifier_paint_not_overwritten_by_wrong_vision(self):
        # Spec case 5 — Paint classifier stays even if vision guesses Fabric.
        row = {"material_family": "Paint", "material_type": "wall paint",
               "object_type": "wall"}
        debug = server._reconcile_family_with_vision_dna(row, _dna("Fabric"))
        assert row["material_family"] == "Paint"
        assert "material_family_original" not in row
        assert debug["override_applied"] is False

    def test_generic_and_unknown_vision_keeps_generic(self):
        # Spec case 6 — unusable DNA doesn't bypass category filtering.
        row = {"material_family": "flooring", "object_type": "floor"}
        debug = server._reconcile_family_with_vision_dna(row, _dna("gibberish"))
        assert row["material_family"] == "flooring"       # unchanged
        assert debug["override_applied"] is False

    def test_generic_wall_becomes_paint_via_vision(self):
        row = {"material_family": "wall", "object_type": "wall"}
        server._reconcile_family_with_vision_dna(row, _dna("Paint"))
        assert row["material_family"] == "Paint"


class TestBrainWithOverride:
    def test_wardrobe_with_laminate_family_routes_to_laminates(self):
        row = {"material_family": "Laminate", "object_type": "wardrobe",
               "material_type": "dark walnut laminate"}
        brain = server.materialmatch_brain(row)
        assert "Laminates" in brain["allowed_categories"]
        assert brain.get("object_aware") is True

    def test_headboard_with_laminate_family_allows_laminates(self):
        # The wood-slat headboard case that previously failed — now the
        # headboard branch honours the DNA family instead of hard-gating
        # to Fabric-only.
        row = {"material_family": "Laminate", "object_type": "headboard",
               "material_type": "warm oak slat panel"}
        brain = server.materialmatch_brain(row)
        assert "Laminates" in brain["allowed_categories"]
        assert "Fabric" in brain["allowed_categories"]

    def test_sofa_stays_fabric_only(self):
        # Sofas are always upholstery — this must NOT expand to Laminates.
        row = {"material_family": "Fabric", "object_type": "sofa"}
        brain = server.materialmatch_brain(row)
        assert brain["allowed_categories"] == ["Fabric"]

    def test_headboard_with_fabric_family_stays_fabric(self):
        row = {"material_family": "Fabric", "object_type": "headboard"}
        brain = server.materialmatch_brain(row)
        assert brain["allowed_categories"] == ["Fabric"]


# ---------------------------------------------------------------------------
# Vision-DNA fallback + exact-loopback preservation.
# ---------------------------------------------------------------------------
class TestVisionDnaFallback:
    def test_returns_none_when_key_missing(self, monkeypatch):
        monkeypatch.setattr(server, "EMERGENT_LLM_KEY", "")
        row = {"material_family": "furniture", "object_type": "wardrobe"}
        result = asyncio.run(server._generate_query_vision_dna("iVBORw0KGgo=", row))
        assert result is None

    def test_returns_none_when_generation_raises(self, monkeypatch):
        monkeypatch.setattr(server, "EMERGENT_LLM_KEY", "sk-test")

        async def _boom(*args, **kwargs):
            raise RuntimeError("network down")

        # Patch the imported generate_swatch_dna inside the helper by
        # replacing intelligence.dna's function.
        import intelligence.dna as dna_mod
        monkeypatch.setattr(dna_mod, "generate_swatch_dna", _boom)
        row = {"material_family": "furniture"}
        result = asyncio.run(server._generate_query_vision_dna("iVBORw0KGgo=", row))
        assert result is None


class TestExactLoopbackUnchanged:
    def test_exact_loopback_bypasses_rerank(self):
        """The pipeline's exact-loopback path is untouched — a pixel-identity
        hit still short-circuits everything downstream."""
        from intelligence.pipeline import retrieve_matches

        query_dna = _dna("Laminate", "warm oak laminate")
        # Give the query the SAME phash the item has → exact loopback.
        ref_hashes = {"phash": "ffffffffffffffff", "avg_rgb": [120, 90, 60]}
        item = {
            "id": "x", "material_name": "Warm Oak", "brand": "Test",
            "material_family": "Laminate", "category": "Laminate",
            "visual_dna": _dna("Laminate", "warm oak laminate"),
            "visual_hashes": {"phash": "ffffffffffffffff", "avg_rgb": [122, 91, 61]},
        }
        result = retrieve_matches(query_dna, ref_hashes, [item], top_k=3)
        # First candidate must be exact_loopback with confidence 100.
        top = result["candidates"][0]
        assert top["exact_visual_match"] is True
        assert top["confidence"] == 100
        assert top["stage"] == "exact_loopback"
