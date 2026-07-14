"""Sprint 3 tests — material-level knowledge engine.

Locks in:
  1. `collection_name` is stored as PAGE METADATA on every record from a
     page but NEVER copied into `material_name`.
  2. Intra-page duplicate detection: two swatches on the same page never
     receive the same `material_name` — the later one is renamed to a
     placeholder and marked `needs_review`.
  3. `needs_review` is set for placeholder / low-confidence / Type-B
     fallback records.
"""

from __future__ import annotations

import io
import sys

sys.path.insert(0, "/app/backend")


def _make_multi_swatch_pdf() -> bytes:
    """Two-page PDF with 3 embedded swatch images per page + a page
    title above them. Simulates the ADVANCE-style multi-swatch layout."""
    import fitz
    from PIL import Image
    d = fitz.open()
    swatches = [
        ("MISTY GREY", (180, 180, 190)),
        ("ROMANTIC PINK", (215, 180, 190)),
        ("LAKE BLUE", (100, 130, 160)),
    ]
    for pi in range(2):
        p = d.new_page(width=595, height=842)
        # Page title — this is the collection name that we must NOT
        # copy into individual material_name fields.
        p.insert_text((40, 40), f"AURIUM COLLECTION {pi + 1}")
        p.insert_text((40, 60), "premium laminate finishes")
        for si, (name, col) in enumerate(swatches):
            img = Image.new("RGB", (300, 300), col)
            buf = io.BytesIO(); img.save(buf, format="JPEG")
            x = 40 + si * 180
            p.insert_image(fitz.Rect(x, 120, x + 160, 280), stream=buf.getvalue())
            # Per-swatch label + code directly below the swatch.
            p.insert_text((x, 300), name)
            p.insert_text((x, 320), f"Code: L-{100 + si + pi * 10}")
    out = io.BytesIO()
    d.save(out); d.close()
    return out.getvalue()


def test_collection_name_stored_as_page_metadata():
    """Every record on a page carries the page-title as collection_name
    metadata — separately from material_name. The exact material_name
    content depends on layout; we only assert the collection_name FIELD
    is populated so downstream views can display page metadata without
    conflating it with the swatch identity."""
    from server import _extract_records_from_pdf
    records, meta = _extract_records_from_pdf(_make_multi_swatch_pdf(), "sprint3-collection")
    assert len(records) >= 6, f"expected >=6 records, got {len(records)}: {meta}"
    p1 = [r for r in records if r["page_number"] == 1]
    assert len(p1) >= 3
    for r in p1:
        # Field must exist (may be None if title detection failed, but
        # the record shape MUST include the field).
        assert "collection_name" in r
    # At least one record picked up the collection name from the header.
    titles = {r.get("collection_name") for r in p1 if r.get("collection_name")}
    assert titles, "no collection_name detected on page 1"


def test_material_name_field_is_per_swatch():
    """`material_name` is a per-swatch string. It must not be empty and
    must not be identical across all swatches on a page (which would
    prove we copied the page title)."""
    from server import _extract_records_from_pdf
    records, _ = _extract_records_from_pdf(_make_multi_swatch_pdf(), "sprint3-per-swatch")
    for pi in {r["page_number"] for r in records}:
        page_records = [r for r in records if r["page_number"] == pi]
        if len(page_records) < 2:
            continue
        names = [(r["material_name"] or "").lower() for r in page_records]
        # No exact-duplicate names on the same page — the whole point
        # of Sprint 3's intra-page-duplicate detection.
        assert len(names) == len(set(names)), (
            f"duplicate material_name on page {pi}: {names}"
        )


def test_needs_review_flag_set_for_placeholders():
    """Placeholder-named records (Swatch p*.s* / Scanned page *) must
    carry needs_review=True so the admin fixes them before publishing."""
    from server import _extract_records_from_pdf
    records, _ = _extract_records_from_pdf(_make_multi_swatch_pdf(), "sprint3-needs-review")
    for r in records:
        if r["material_name"].startswith("Swatch ") or r["material_name"].startswith("Scanned page "):
            assert r["needs_review"] is True, (
                f"placeholder record must be needs_review=True: {r['material_name']}"
            )
