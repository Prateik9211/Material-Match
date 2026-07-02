"""Tests for MaterialMatch V2 upgrades (2026-02-28+):
- Surface-based analysis validator now returns {rows, summary} and accepts
  brands_to_check, vendor_type, sourcing_keywords, procurement_difficulty.
- PDF-page rendering for catalogue matching (PyMuPDF) produces the expected
  candidate shape with page_number and thumbnail data URL.
"""
import importlib
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
server = importlib.import_module("server")
_validate_analysis_payload = server._validate_analysis_payload
_render_pdf_pages_to_candidates = server._render_pdf_pages_to_candidates
PROCUREMENT_DIFFICULTY = server.PROCUREMENT_DIFFICULTY


def _base_row(**over):
    row = {
        "zone": "Headboard Feature Panel",
        "material_family": "wood",
        "material_type": "Fluted walnut laminate panel",
        "color": "Warm walnut brown",
        "texture": "Vertical fluted",
        "finish": "Matt PU",
        "design_style": "Warm modern",
        "keywords": ["walnut", "fluted"],
        "confidence": 85,
    }
    row.update(over)
    return row


def test_v2_accepts_all_new_india_fields():
    payload = {
        "summary": {
            "design_style": "Warm modern",
            "material_palette": "Walnut veneer, textured paint, brass",
            "key_finishes": "Matt PU, satin veneer",
            "sourcing_note": "Indian dealers via Greenlam, Century, Asian Paints.",
        },
        "rows": [_base_row(
            indian_alternative="Walnut laminate on routed MDF (Greenlam range).",
            brands_to_check=["Greenlam", "Merino", "Century", "Royale Touche"],
            vendor_type="Laminate dealer + carpenter/CNC panel fabricator",
            sourcing_keywords=["walnut fluted panel India", "ribbed MDF panel"],
            procurement_difficulty="Medium",
        )],
    }
    out = _validate_analysis_payload(payload)
    r = out["rows"][0]
    assert r["indian_alternative"].startswith("Walnut laminate")
    assert r["brands_to_check"] == ["Greenlam", "Merino", "Century", "Royale Touche"]
    assert r["vendor_type"].startswith("Laminate dealer")
    assert r["sourcing_keywords"][0] == "walnut fluted panel India"
    assert r["procurement_difficulty"] == "Medium"
    s = out["summary"]
    assert s["design_style"] == "Warm modern"
    assert "Greenlam" in s["sourcing_note"]


def test_v2_new_fields_are_optional_and_soft_coerced():
    payload = {"rows": [_base_row(
        procurement_difficulty="EasyPeasy",  # invalid → coerced to None
        brands_to_check="Greenlam",           # wrong type → empty list
        sourcing_keywords=[123, "walnut fluted panel India"],  # mixed → keep valid
    )]}
    out = _validate_analysis_payload(payload)
    r = out["rows"][0]
    assert r["procurement_difficulty"] is None
    assert r["brands_to_check"] == []
    assert r["sourcing_keywords"] == ["walnut fluted panel India"]


def test_v2_summary_missing_is_empty_shape():
    payload = {"rows": [_base_row()]}
    out = _validate_analysis_payload(payload)
    assert out["summary"] == {
        "design_style": "", "material_palette": "", "key_finishes": "", "sourcing_note": "",
    }


def test_procurement_difficulty_enum_values():
    for val in PROCUREMENT_DIFFICULTY:
        payload = {"rows": [_base_row(procurement_difficulty=val)]}
        assert _validate_analysis_payload(payload)["rows"][0]["procurement_difficulty"] == val


def _make_tiny_pdf(pages: int = 3) -> bytes:
    """Build a minimal in-memory PDF with `pages` blank A5 pages."""
    import fitz
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=420, height=595)  # A5
    buf = doc.tobytes()
    doc.close()
    return buf


def test_render_pdf_pages_produces_candidates():
    pdf_bytes = _make_tiny_pdf(pages=3)
    warnings = []
    cands = _render_pdf_pages_to_candidates(pdf_bytes, "test-catalogue.pdf", warnings)
    assert len(cands) == 3
    for i, c in enumerate(cands):
        assert c["name"] == "test-catalogue.pdf"
        assert c["page_number"] == i + 1
        assert c["thumb_b64"].startswith("data:image/jpeg;base64,")
        assert isinstance(c["b64"], str) and len(c["b64"]) > 100
        assert c["size"] > 0


def test_render_pdf_caps_at_max_pages():
    pdf_bytes = _make_tiny_pdf(pages=12)
    warnings = []
    cands = _render_pdf_pages_to_candidates(pdf_bytes, "big.pdf", warnings)
    assert len(cands) == server.MATCH_PDF_MAX_PAGES  # default 8
    # Warning surfaces the truncation
    assert any("only the first" in w for w in warnings)


def test_render_pdf_rejects_oversize():
    # Fake an oversize payload with the right MIME
    big = b"%PDF-fake" + b"\0" * (server.MATCH_PDF_MAX_FILE_BYTES + 1)
    warnings = []
    cands = _render_pdf_pages_to_candidates(big, "huge.pdf", warnings)
    assert cands == []
    assert any("larger than" in w for w in warnings)


def test_render_pdf_handles_bad_bytes():
    warnings = []
    cands = _render_pdf_pages_to_candidates(b"not a pdf at all", "bad.pdf", warnings)
    assert cands == []
    assert any("Could not open" in w for w in warnings)
