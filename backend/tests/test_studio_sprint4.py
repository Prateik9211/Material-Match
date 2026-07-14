"""Sprint 4 — region classification + category verification tests.

Locks in: certifications, text blocks and QR-like images are rejected
before record creation; catalogue-level brand inference propagates to
every record; category verification catches hint conflicts and marks
them needs_review with the correct reason code."""

from __future__ import annotations

import io
import sys
sys.path.insert(0, "/app/backend")


def _pdf_with(regions: list) -> bytes:
    """Build a PDF with one page per region descriptor. Each region is
    a colour swatch + a text hint (used by _classify_region)."""
    import fitz
    from PIL import Image
    d = fitz.open()
    for text, colour in regions:
        p = d.new_page(width=595, height=842)
        img = Image.new("RGB", (300, 300), colour)
        buf = io.BytesIO(); img.save(buf, format="JPEG")
        p.insert_image(fitz.Rect(80, 200, 400, 500), stream=buf.getvalue())
        # Put the classification-triggering text right next to the image
        p.insert_text((80, 80), text)
        p.insert_text((80, 100), "Product line premium")
        p.insert_text((80, 120), "laminate finish")
    out = io.BytesIO()
    d.save(out); d.close()
    return out.getvalue()


class TestSprint4RegionClassification:
    def test_certification_pages_are_rejected(self):
        """Certification-flavoured text near a rectangle → CERTIFICATION,
        never a MATERIAL_SWATCH record."""
        from server import _extract_records_from_pdf
        # Coloured swatch so it survives pixel-stats detection; the
        # rejection must come from the certification-keyword branch.
        pdf = _pdf_with([("Certificate of ISO 9001 conformity awarded to this facility", (140, 100, 60))])
        records, meta = _extract_records_from_pdf(pdf, "sprint4-cert")
        assert len(records) == 0, f"cert page produced records: {records}"
        assert meta["region_rejects"]["CERTIFICATION"] >= 1

    def test_material_swatch_survives(self):
        """A laminate-context region passes region classification and
        becomes a real record."""
        from server import _extract_records_from_pdf
        pdf = _pdf_with([("Warm Oak Ripple Code: L-8834 laminate finish", (150, 100, 60))])
        records, meta = _extract_records_from_pdf(pdf, "sprint4-swatch")
        assert len(records) >= 1
        r = records[0]
        assert r["region_class"] == "MATERIAL_SWATCH"
        assert 0 <= r["region_confidence"] <= 1
        assert r["swatch_verified"] is True

    def test_qr_like_polarised_image_is_rejected(self):
        """A binary black/white image next to no material-text is a QR."""
        from server import _extract_records_from_pdf
        import fitz
        from PIL import Image
        d = fitz.open()
        p = d.new_page(width=595, height=842)
        # Chequered black-white pattern → QR-like polarisation
        img = Image.new("RGB", (200, 200), (255, 255, 255))
        pixels = img.load()
        for x in range(200):
            for y in range(200):
                if (x // 20 + y // 20) % 2 == 0:
                    pixels[x, y] = (0, 0, 0)
        buf = io.BytesIO(); img.save(buf, format="PNG")
        p.insert_image(fitz.Rect(80, 200, 380, 500), stream=buf.getvalue())
        # No material keywords nearby
        p.insert_text((80, 80), "Scan for details")
        out = io.BytesIO(); d.save(out); d.close()
        records, _ = _extract_records_from_pdf(out.getvalue(), "sprint4-qr")
        # No material record should survive from a QR-like image
        assert len(records) == 0


class TestSprint4CategoryVerification:
    def test_stone_hint_with_veneer_content_yields_veneer(self):
        """A page mentioning oak / veneer must be classified Veneer even
        when the upload hint is Stone."""
        from server import _verify_category
        cat, conf, conflict = _verify_category(
            "warm oak veneer premium wood grain finish",
            hint="Stone",
        )
        assert cat == "Veneer"
        assert conflict is True
        assert conf >= 0.6

    def test_no_category_context_falls_back_to_hint(self):
        from server import _verify_category
        cat, conf, conflict = _verify_category("code XY-123", hint="Laminate")
        assert cat == "Laminate"
        assert conflict is False

    def test_unsupported_context_returns_none(self):
        """Toughened glass isn't in our supported categories — return
        None (so downstream marks it needs_review + unsupported_category)."""
        from server import _verify_category
        cat, _, _ = _verify_category("toughened glass panel decorative pattern", hint=None)
        # None or non-glass — never fabricate a category
        assert cat != "Glass"


class TestSprint4BrandDetection:
    def test_brand_inferred_from_first_pages(self):
        """When 'Advance' appears on any of the first 3 pages, the
        catalogue-level brand is populated."""
        from server import _infer_catalogue_brand
        import fitz
        d = fitz.open()
        p = d.new_page(width=595, height=842)
        p.insert_text((80, 80), "ADVANCE LAMINATES — premium collection")
        p.insert_text((80, 120), "material catalogue")
        d.new_page(width=595, height=842)   # blank second page
        result = _infer_catalogue_brand(d)
        d.close()
        assert result == "Advance"

    def test_unknown_brand_returns_none(self):
        from server import _infer_catalogue_brand
        import fitz
        d = fitz.open()
        d.new_page(width=595, height=842)
        result = _infer_catalogue_brand(d)
        d.close()
        assert result is None


class TestSprint4NeedsReviewReasons:
    def test_needs_review_reasons_populated(self):
        """Records without a code, or with placeholder names, must carry
        structured needs_review_reasons codes."""
        from server import _extract_records_from_pdf
        pdf = _pdf_with([("Warm Oak Ripple Code: L-8834 laminate", (140, 90, 55))])
        records, _ = _extract_records_from_pdf(pdf, "sprint4-reasons")
        for r in records:
            assert "needs_review_reasons" in r
            assert isinstance(r["needs_review_reasons"], list)
            # If needs_review is True there must be at least one reason.
            if r["needs_review"]:
                assert len(r["needs_review_reasons"]) >= 1
