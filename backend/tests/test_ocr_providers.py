"""Tests for the OCR provider chain (Tesseract → GPT-4o-mini fallback).

These tests validate the provider abstraction WITHOUT hitting the real
GPT-4o-mini API. A `FakeVisionProvider` is monkey-patched into the chain
so we can verify: fallback triggers when local OCR is empty, chain
picks the first available provider, downstream sees identical shape."""

from __future__ import annotations

import io
import sys

import pytest
from PIL import Image, ImageDraw


sys.path.insert(0, "/app/backend")

from ocr_providers import (  # noqa: E402
    OCRProviderChain,
    TesseractProvider,
    GPT4oMiniVisionProvider,
    get_ocr_provider_chain,
    reset_ocr_provider_chain,
)


def _png_bytes(text: str) -> bytes:
    img = Image.new("RGB", (600, 200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((30, 30), text, fill=(0, 0, 0))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


class FakeProvider:
    def __init__(self, name: str, available: bool, output: str = "") -> None:
        self.name = name
        self._available = available
        self._output = output
        self.call_count = 0

    def is_available(self) -> bool:
        return self._available

    def transcribe_bytes(self, png_bytes: bytes) -> str:  # noqa: ARG002
        self.call_count += 1
        return self._output


class TestOCRProviderChain:
    def test_first_available_provider_used(self):
        primary = FakeProvider("primary", available=True, output="hello world")
        fallback = FakeProvider("fallback", available=True, output="ignored")
        chain = OCRProviderChain([primary, fallback])
        text, used = chain.transcribe(_png_bytes("x"))
        assert text == "hello world"
        assert used == "primary"
        assert primary.call_count == 1
        assert fallback.call_count == 0

    def test_falls_back_when_primary_empty(self):
        primary = FakeProvider("primary", available=True, output="")
        fallback = FakeProvider("fallback", available=True, output="from-fallback")
        chain = OCRProviderChain([primary, fallback])
        text, used = chain.transcribe(_png_bytes("x"))
        assert text == "from-fallback"
        assert used == "fallback"
        assert primary.call_count == 1
        assert fallback.call_count == 1

    def test_skips_unavailable_provider(self):
        p1 = FakeProvider("p1", available=False, output="nope")
        p2 = FakeProvider("p2", available=True, output="ok-transcript")
        chain = OCRProviderChain([p1, p2])
        text, used = chain.transcribe(_png_bytes("x"))
        assert text == "ok-transcript"
        assert used == "p2"
        assert p1.call_count == 0
        assert p2.call_count == 1

    def test_returns_none_when_no_providers_available(self):
        p = FakeProvider("only", available=False, output="")
        chain = OCRProviderChain([p])
        text, used = chain.transcribe(_png_bytes("x"))
        assert text == ""
        assert used == "none"

    def test_short_text_triggers_fallback(self):
        """A provider returning a couple of characters is treated as
        useless and the next provider is tried."""
        primary = FakeProvider("primary", available=True, output="ab")
        fallback = FakeProvider("fallback", available=True, output="hello world")
        chain = OCRProviderChain([primary, fallback], min_useful_chars=6)
        text, used = chain.transcribe(_png_bytes("x"))
        assert text == "hello world"
        assert used == "fallback"

    def test_provider_crash_does_not_break_chain(self):
        class Boom:
            name = "boom"
            def is_available(self): return True
            def transcribe_bytes(self, _): raise RuntimeError("kaboom")
        working = FakeProvider("ok", available=True, output="survived")
        chain = OCRProviderChain([Boom(), working])
        text, used = chain.transcribe(_png_bytes("x"))
        assert text == "survived"
        assert used == "ok"


class TestTesseractProvider:
    def test_availability_check_cached(self, monkeypatch):
        import shutil as _shutil
        calls = {"n": 0}
        def fake_which(name):
            calls["n"] += 1
            return "/usr/bin/tesseract" if name == "tesseract" else None
        monkeypatch.setattr(_shutil, "which", fake_which)
        p = TesseractProvider()
        assert p.is_available() is True
        assert p.is_available() is True
        # Cached — only one shutil.which call regardless of how many
        # times we ask.
        assert calls["n"] == 1


class TestGPT4oMiniVisionProvider:
    def test_unavailable_without_api_key(self):
        p = GPT4oMiniVisionProvider(api_key="")
        assert p.is_available() is False
        assert p.transcribe_bytes(_png_bytes("x")) == ""

    def test_available_with_api_key(self):
        p = GPT4oMiniVisionProvider(api_key="fake-key")
        assert p.is_available() is True


class TestGlobalChain:
    def test_get_provider_chain_singleton(self):
        reset_ocr_provider_chain()
        c1 = get_ocr_provider_chain()
        c2 = get_ocr_provider_chain()
        assert c1 is c2
        reset_ocr_provider_chain()

    def test_reset_creates_fresh_instance(self):
        c1 = get_ocr_provider_chain()
        reset_ocr_provider_chain()
        c2 = get_ocr_provider_chain()
        assert c1 is not c2
        reset_ocr_provider_chain()
