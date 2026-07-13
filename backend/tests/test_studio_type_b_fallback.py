"""Sprint post-freeze test — Type-B (scanned full-page-image) fallback.

Verifies that catalogues shaped like `0.8MM-FOLDER.pdf` (each page is a
single full-page raster scan with no embedded per-swatch images) still
produce at least one record per page instead of returning zero records
for the whole catalogue."""

from __future__ import annotations

import io
import sys

import pytest

sys.path.insert(0, "/app/backend")


def _make_scanned_pdf(n_pages: int = 3) -> bytes:
    """Build a PDF where each page is a SINGLE full-page raster image
    (the Type-B catalogue shape)."""
    import fitz
    from PIL import Image
    d = fitz.open()
    for i in range(n_pages):
        # Portrait A4-ish page
        p = d.new_page(width=595, height=842)
        img = Image.new("RGB", (1600, 2200), (200 - i * 20, 180, 150))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        # Fill 95% of the page with one image — this is what our per-image
        # geometry filter previously rejected as "hero banner".
        p.insert_image(fitz.Rect(15, 15, 580, 827), stream=buf.getvalue())
    out = io.BytesIO()
    d.save(out); d.close()
    return out.getvalue()


def test_type_b_scanned_pdf_produces_one_record_per_page():
    """A scanned catalogue with N pages must produce N records via the
    page-level fallback, not zero."""
    from server import _extract_records_from_pdf
    pdf = _make_scanned_pdf(n_pages=3)
    records, meta = _extract_records_from_pdf(pdf, upload_id="type-b-test-1")
    assert len(records) == 3, f"expected 3 page-level records, got {len(records)}: {meta}"
    assert meta["page_level_fallback_count"] == 3
    assert meta["extraction_mode"] in ("ocr", "text", "text+ocr")
    assert meta["failure_reason"] is None
    for i, r in enumerate(records, start=1):
        assert r["page_number"] == i
        assert r["material_name"], f"page {i} missing material_name"
        assert r["color_hex"], f"page {i} missing color_hex"


def test_type_b_meta_carries_fallback_count():
    """The `page_level_fallback_count` telemetry must be recorded so
    admins can see how many records came from the Type-B fallback path
    (i.e. which need extra manual review)."""
    from server import _extract_records_from_pdf
    pdf = _make_scanned_pdf(n_pages=2)
    _, meta = _extract_records_from_pdf(pdf, upload_id="type-b-test-2")
    assert "page_level_fallback_count" in meta
    assert meta["page_level_fallback_count"] == 2
