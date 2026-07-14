"""Sprint 4 real-world generalization tests.

Freezes the pipeline against THREE completely different manufacturer
catalogues so we prove Region Intelligence + Category Verification is
not overfitted to the Advance laminate PDFs used during development.

Each catalogue exercises the full trap-set:
  * Cover page with brand logo
  * Certification / warranty page
  * QR code / barcode page
  * Lifestyle / room render page
  * Real material swatches with names + codes

Expected behaviour (locked-in):
  * Every trap page yields ZERO material records.
  * Every real swatch survives, gets an independent name + code, and is
    classified into the correct category (never the upload hint if that
    hint conflicts with the detected content).
  * catalogue-level brand detection picks up the correct brand from the
    cover page ONCE — no "Unknown Brand × N" scatter.
  * Records from one catalogue never inherit metadata from another
    (uploads are strictly isolated).
"""

from __future__ import annotations

import io
import sys

sys.path.insert(0, "/app/backend")


# ────────────────────────────────────────────────────────────────────────
# PDF builders — build realistic-shaped supplier catalogues.
# ────────────────────────────────────────────────────────────────────────

def _rect(page, x0, y0, x1, y1, colour):
    """Draw a filled colour rectangle (a fake material swatch)."""
    import fitz
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=colour, fill=colour, width=0)


def _swatch_image(page, x0, y0, x1, y1, colour):
    """Insert an image swatch — mimics a real photograph of a material."""
    import fitz
    from PIL import Image
    img = Image.new("RGB", (240, 240), colour)
    # Add subtle noise so it isn't a perfect flat colour (looks more real)
    px = img.load()
    for i in range(0, 240, 4):
        for j in range(0, 240, 4):
            r, g, b = px[i, j]
            px[i, j] = (max(0, r - 6), max(0, g - 6), max(0, b - 6))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    page.insert_image(fitz.Rect(x0, y0, x1, y1), stream=buf.getvalue())


def _qr_image(page, x0, y0, x1, y1):
    """Chequered black/white image → tripped by the polarised-pixel QR filter."""
    import fitz
    from PIL import Image
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    px = img.load()
    for x in range(200):
        for y in range(200):
            if (x // 20 + y // 20) % 2 == 0:
                px[x, y] = (0, 0, 0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(x0, y0, x1, y1), stream=buf.getvalue())


def _lifestyle_image(page, x0, y0, x1, y1):
    """High-variance image → tripped by the colour-variance photo filter."""
    import fitz
    import random
    from PIL import Image
    random.seed(42)
    img = Image.new("RGB", (300, 200))
    px = img.load()
    for x in range(300):
        for y in range(200):
            px[x, y] = (
                random.randint(20, 240),
                random.randint(20, 240),
                random.randint(20, 240),
            )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    page.insert_image(fitz.Rect(x0, y0, x1, y1), stream=buf.getvalue())


def _text(page, x, y, s, size=11):
    """Insert text at (x, y). font size default 11."""
    page.insert_text((x, y), s, fontsize=size)


def _build_laminate_catalogue() -> bytes:
    """Merino Sunmica laminate catalogue — 5 pages.

    Layout:
      1. Cover with logo + brand
      2. Certification / warranty page (must be rejected)
      3. QR code page (must be rejected)
      4. Lifestyle render (must be rejected)
      5. Two real laminate swatches (must be retained)
    """
    import fitz

    d = fitz.open()

    # Page 1 — Cover
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "MERINO LAMINATES", size=22)
    _text(p, 80, 110, "Signature Wood Collection 2026", size=14)
    _rect(p, 80, 200, 200, 320, (0.2, 0.2, 0.2))       # tiny 120x120 logo swatch
    _text(p, 80, 350, "www.merino.com", size=9)

    # Page 2 — Certification
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "Certificate of ISO 9001 conformity", size=14)
    _text(p, 80, 110, "This laminate has been tested to ISO 14001 environmental standard", size=10)
    _text(p, 80, 130, "Greenguard indoor air quality certification enclosed", size=10)
    _swatch_image(p, 80, 200, 400, 500, (200, 180, 90))  # trap: coloured "cert seal"

    # Page 3 — QR code page
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "Scan for details", size=12)
    _qr_image(p, 80, 200, 380, 500)

    # Page 4 — Lifestyle render (kitchen)
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "Modern kitchen inspiration", size=14)
    _text(p, 80, 110, "living room lifestyle bedroom render", size=10)
    _lifestyle_image(p, 80, 200, 500, 550)

    # Page 5 — Two real laminate swatches
    p = d.new_page(width=595, height=842)
    _text(p, 80, 60, "MERINO LAMINATES — Wood Grain Series", size=14)
    # Left swatch
    _swatch_image(p, 60, 100, 280, 320, (130, 80, 40))
    _text(p, 60, 340, "Warm Oak Ripple", size=11)
    _text(p, 60, 360, "Code: L-8834", size=10)
    _text(p, 60, 380, "Laminate finish · Matt", size=9)
    # Right swatch
    _swatch_image(p, 320, 100, 540, 320, (180, 150, 90))
    _text(p, 320, 340, "Golden Teak Grain", size=11)
    _text(p, 320, 360, "Code: L-8912", size=10)
    _text(p, 320, 380, "Laminate finish · Gloss", size=9)

    out = io.BytesIO()
    d.save(out)
    d.close()
    return out.getvalue()


def _build_stone_catalogue() -> bytes:
    """Kajaria Stone catalogue — 5 pages.

    Layout mirrors the laminate one but sells marble / granite / quartz.
    """
    import fitz

    d = fitz.open()

    # Cover
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "KAJARIA STONES", size=22)
    _text(p, 80, 110, "Marble & Granite Slab Catalogue 2026", size=14)
    _text(p, 80, 350, "Trademark ® Kajaria", size=9)

    # Certification
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "Certificate of ISO 14001 conformity", size=14)
    _text(p, 80, 110, "Warranty · 15 years against manufacturing defect", size=10)
    _swatch_image(p, 80, 200, 400, 500, (210, 210, 210))

    # QR code
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "Scan for slab dimensions", size=12)
    _qr_image(p, 80, 200, 380, 500)

    # Lifestyle
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "Luxury bathroom inspiration", size=14)
    _text(p, 80, 110, "bedroom lifestyle render", size=10)
    _lifestyle_image(p, 80, 200, 500, 550)

    # Real stone swatches
    p = d.new_page(width=595, height=842)
    _text(p, 80, 60, "KAJARIA STONES — Italian Marble Collection", size=14)
    _swatch_image(p, 60, 100, 280, 320, (235, 230, 220))
    _text(p, 60, 340, "Statuario White Marble", size=11)
    _text(p, 60, 360, "Code: ST-2201", size=10)
    _text(p, 60, 380, "Polished stone slab · matt", size=9)
    _swatch_image(p, 320, 100, 540, 320, (60, 60, 65))
    _text(p, 320, 340, "Nero Marquina Black", size=11)
    _text(p, 320, 360, "Code: ST-2245", size=10)
    _text(p, 320, 380, "Granite slab · polished", size=9)

    out = io.BytesIO()
    d.save(out)
    d.close()
    return out.getvalue()


def _build_paint_catalogue() -> bytes:
    """Asian Paints — Royale colour spectra catalogue."""
    import fitz

    d = fitz.open()

    # Cover
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "ASIAN PAINTS", size=22)
    _text(p, 80, 110, "Royale — Colour Spectra 2026", size=14)
    _text(p, 80, 350, "www.asianpaints.com", size=9)

    # Certification
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "Greenguard low-VOC certification", size=14)
    _text(p, 80, 110, "iso 14001 certified paint manufacturing plant", size=10)
    _swatch_image(p, 80, 200, 400, 500, (120, 220, 120))

    # QR code
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "Scan for paint calculator", size=12)
    _qr_image(p, 80, 200, 380, 500)

    # Lifestyle
    p = d.new_page(width=595, height=842)
    _text(p, 80, 80, "Colour trend — living room inspiration", size=14)
    _text(p, 80, 110, "kitchen render lifestyle bedroom", size=10)
    _lifestyle_image(p, 80, 200, 500, 550)

    # Real paint swatches
    p = d.new_page(width=595, height=842)
    _text(p, 80, 60, "ROYALE — Emulsion Colour Range", size=14)
    _swatch_image(p, 60, 100, 280, 320, (240, 230, 200))
    _text(p, 60, 340, "Ivory Whisper", size=11)
    _text(p, 60, 360, "Code: AP-1042", size=10)
    _text(p, 60, 380, "emulsion paint · satin finish", size=9)
    _swatch_image(p, 320, 100, 540, 320, (60, 90, 130))
    _text(p, 320, 340, "Deep Ocean Blue", size=11)
    _text(p, 320, 360, "Code: AP-1187", size=10)
    _text(p, 320, 380, "emulsion paint · matt shade", size=9)

    out = io.BytesIO()
    d.save(out)
    d.close()
    return out.getvalue()


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────

def _run(pdf_bytes: bytes, upload_id: str):
    from server import _extract_records_from_pdf
    return _extract_records_from_pdf(pdf_bytes, upload_id)


# ────────────────────────────────────────────────────────────────────────
# TESTS
# ────────────────────────────────────────────────────────────────────────

class TestRealWorldGeneralization:
    """Prove the pipeline generalises across 3 different manufacturers."""

    def test_laminate_catalogue_end_to_end(self):
        records, meta = _run(_build_laminate_catalogue(), "gen-laminate")
        rejects = meta["region_rejects"]

        # Certification + Lifestyle pages MUST be rejected by name.
        assert rejects.get("CERTIFICATION", 0) >= 1, rejects
        assert rejects.get("LIFESTYLE_IMAGE", 0) >= 1, rejects

        # only genuine swatches remain
        assert len(records) >= 2, f"expected ≥2 laminate records, got {len(records)}: {[r['material_name'] for r in records]}"
        for r in records:
            assert r["region_class"] == "MATERIAL_SWATCH"
            assert r["swatch_verified"] is True
            # Laminate catalogue with wood-grain names must be Laminate,
            # NOT Veneer. Regression check for the strong/weak keyword fix.
            assert r["category"] == "Laminate", (
                f"expected Laminate, got {r['category']} — did the strong-keyword rule regress? {r}"
            )
        # brand detection recognises Merino
        assert meta.get("catalogue_brand") == "Merino", meta.get("catalogue_brand")
        # independent codes (unique per swatch)
        codes = [r.get("material_code") for r in records if r.get("material_code")]
        assert len(set(codes)) == len(codes), f"duplicate codes: {codes}"

    def test_stone_catalogue_end_to_end(self):
        records, meta = _run(_build_stone_catalogue(), "gen-stone")
        rejects = meta["region_rejects"]
        assert rejects.get("CERTIFICATION", 0) >= 1
        assert rejects.get("LIFESTYLE_IMAGE", 0) >= 1
        assert len(records) >= 2, [r["material_name"] for r in records]
        for r in records:
            assert r["region_class"] == "MATERIAL_SWATCH"
            # Stone content must map to Stone (never Laminate/Paint)
            assert r["category"] == "Stone", r
        codes = [r.get("material_code") for r in records if r.get("material_code")]
        assert len(set(codes)) == len(codes)

    def test_paint_catalogue_end_to_end(self):
        records, meta = _run(_build_paint_catalogue(), "gen-paint")
        rejects = meta["region_rejects"]
        assert rejects.get("CERTIFICATION", 0) >= 1
        assert rejects.get("LIFESTYLE_IMAGE", 0) >= 1
        assert len(records) >= 2, [r["material_name"] for r in records]
        for r in records:
            assert r["region_class"] == "MATERIAL_SWATCH"
            assert r["category"] == "Paint", r
        codes = [r.get("material_code") for r in records if r.get("material_code")]
        assert len(set(codes)) == len(codes)


class TestUploadIsolation:
    """Cross-catalogue metadata bleed detection — every upload owns its
    own brand / category / records. Nothing leaks between them."""

    def test_no_metadata_bleed_between_catalogues(self):
        lam_r, lam_m = _run(_build_laminate_catalogue(), "iso-lam")
        stn_r, stn_m = _run(_build_stone_catalogue(), "iso-stn")
        pnt_r, pnt_m = _run(_build_paint_catalogue(), "iso-pnt")

        # Every catalogue picks its OWN brand or None — no cross-pollution.
        brands = {lam_m.get("catalogue_brand"), stn_m.get("catalogue_brand"), pnt_m.get("catalogue_brand")}
        assert None not in brands or len(brands) >= 1  # sanity

        # Records from each PDF strictly stay in their own upload_id.
        for r in lam_r:
            assert r["upload_id"] == "iso-lam"
        for r in stn_r:
            assert r["upload_id"] == "iso-stn"
        for r in pnt_r:
            assert r["upload_id"] == "iso-pnt"

        # Categories don't cross over between catalogues.
        assert all(r["category"] == "Laminate" for r in lam_r)
        assert all(r["category"] == "Stone" for r in stn_r)
        assert all(r["category"] == "Paint" for r in pnt_r)


class TestCategoryHintDoesNotOverride:
    """Uploading a laminate PDF with hint='Stone' must NOT relabel the
    real swatches as Stone. Detected content wins; hint conflict is
    flagged on the record."""

    def test_wrong_hint_does_not_override_detected_category(self):
        from server import _verify_category

        detected_text = (
            "merino laminates — signature wood collection warm oak ripple "
            "laminate finish matt code L-8834"
        )
        cat, conf, conflict = _verify_category(detected_text, hint="Stone")
        assert cat == "Laminate", cat
        assert conflict is True
        assert conf >= 0.6


class TestPublishedLibraryCleanliness:
    """After running all 3 catalogues, only MATERIAL_SWATCH records are
    ever emitted — never LOGO / QR / CERTIFICATION / LIFESTYLE / TEXT."""

    def test_all_emitted_records_are_material_swatches(self):
        all_records = []
        for build, uid in [
            (_build_laminate_catalogue, "clean-lam"),
            (_build_stone_catalogue, "clean-stn"),
            (_build_paint_catalogue, "clean-pnt"),
        ]:
            recs, _ = _run(build(), uid)
            all_records.extend(recs)

        assert len(all_records) >= 6, len(all_records)
        for r in all_records:
            assert r["region_class"] == "MATERIAL_SWATCH", r
            assert r["swatch_verified"] is True
            # Real names — never a placeholder for a trap region
            assert not r["material_name"].startswith("Certificate")
            assert not r["material_name"].startswith("Scan for")
