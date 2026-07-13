"""OCR provider abstraction for MaterialMatch Studio.

Design goals
------------
1. **Downstream stays unchanged.** Every provider returns plain UTF-8 text
   given raw PNG/JPEG bytes. The material-extraction pipeline only ever
   sees a `str`, so swapping providers is transparent.
2. **Local-first, cloud-fallback.** We try Tesseract when the binary is
   available (fast, free, offline). If the binary is missing or the
   result is empty on a page that clearly contains printed text, we
   fall back to `gpt-4o-mini` vision via the Emergent Universal Key,
   which is guaranteed persistent across preview restarts, pod
   recycles, fresh deploys and application redeployments.
3. **Cost-controlled cloud calls.** GPT-4o-mini vision is only invoked
   when local OCR returns something useless (empty / too-short). We
   also downscale the image before base-64 encoding to keep token
   consumption bounded (~$0.003–0.005 per page at 2025 pricing).
4. **Zero background-task blocking.** All providers expose a
   `transcribe_bytes(png_bytes) -> str` interface designed to run
   inside `asyncio.to_thread(...)` from the extractor.

The public surface is deliberately tiny:

    provider = get_ocr_provider_chain()
    text, used = provider.transcribe(png_bytes)         # sync
    text, used = await provider.atranscribe(png_bytes)  # async

`used` is a short string ("tesseract" | "gpt-4o-mini" | "none") that we
persist on each `ke_records` row so admins can see which provider
extracted a particular swatch's text.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Protocol, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

class OCRProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def transcribe_bytes(self, png_bytes: bytes) -> str: ...


# ---------------------------------------------------------------------------
# Tesseract (local, fast, may not be present in production)
# ---------------------------------------------------------------------------

class TesseractProvider:
    name = "tesseract"

    def __init__(self) -> None:
        self._cached_available: Optional[bool] = None

    def is_available(self) -> bool:
        # Cache the check per-process so we don't fork a subprocess on
        # every page. `shutil.which` is a cheap PATH lookup.
        if self._cached_available is None:
            try:
                self._cached_available = shutil.which("tesseract") is not None
            except Exception:
                self._cached_available = False
        return self._cached_available

    def transcribe_bytes(self, png_bytes: bytes) -> str:
        if not self.is_available():
            return ""
        try:
            import pytesseract  # type: ignore
            from PIL import Image
            img = Image.open(io.BytesIO(png_bytes))
            return (pytesseract.image_to_string(img) or "").strip()
        except Exception:
            logger.exception("tesseract OCR failed")
            return ""


# ---------------------------------------------------------------------------
# GPT-4o-mini Vision (cloud, persistent, requires EMERGENT_LLM_KEY)
# ---------------------------------------------------------------------------

@dataclass
class _VisionConfig:
    """Kept minimal on purpose. If we ever swap to `gemini-3-flash-preview`
    or `claude-sonnet-4-6`, only these two constants change — the
    provider abstraction stays identical."""
    model_provider: str = "openai"
    model_name: str = "gpt-4o-mini"
    # Down-scale the longest side before sending to save vision-token
    # budget. 1400 px is more than enough for the printed text on a
    # catalogue page.
    max_side_px: int = 1400
    jpeg_quality: int = 82
    prompt: str = (
        "You are an OCR engine. Transcribe ALL readable text from this "
        "image of a material catalogue page. Preserve line breaks. "
        "Return ONLY the transcribed text — no commentary, no bullet "
        "points, no formatting."
    )
    system_message: str = "You are a professional OCR system. Output plain text only."


class GPT4oMiniVisionProvider:
    name = "gpt-4o-mini"

    def __init__(self, api_key: Optional[str] = None, config: Optional[_VisionConfig] = None) -> None:
        self._api_key = api_key or os.environ.get("EMERGENT_LLM_KEY", "")
        self._config = config or _VisionConfig()

    def is_available(self) -> bool:
        return bool(self._api_key)

    # -- helpers --------------------------------------------------------

    def _downscale_and_encode(self, png_bytes: bytes) -> str:
        """Downscale + JPEG-encode + base64. Keeps token cost bounded."""
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            w, h = img.size
            m = self._config.max_side_px
            if max(w, h) > m:
                if w >= h:
                    img = img.resize((m, int(h * m / w)), Image.LANCZOS)
                else:
                    img = img.resize((int(w * m / h), m), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=self._config.jpeg_quality, optimize=True)
            return base64.b64encode(out.getvalue()).decode("ascii")
        except Exception:
            # If PIL fails, send the raw bytes as-is (base64 encoded).
            logger.exception("vision downscale failed — sending raw image")
            return base64.b64encode(png_bytes).decode("ascii")

    # -- sync + async transcription ------------------------------------

    def transcribe_bytes(self, png_bytes: bytes) -> str:
        """Synchronous transcription — safe to call from inside
        `asyncio.to_thread`. Never raises: returns "" on any failure so
        the extractor keeps going."""
        if not self.is_available():
            return ""
        try:
            # Local import so unit tests don't need emergentintegrations.
            from emergentintegrations.llm.chat import (
                LlmChat, UserMessage, ImageContent,
            )
            img_b64 = self._downscale_and_encode(png_bytes)
            chat = LlmChat(
                api_key=self._api_key,
                # A fresh session id per page keeps OCR calls stateless
                # (we don't want the model to see prior pages' text).
                session_id=f"studio-ocr-{os.urandom(6).hex()}",
                system_message=self._config.system_message,
            ).with_model(self._config.model_provider, self._config.model_name)
            msg = UserMessage(
                text=self._config.prompt,
                file_contents=[ImageContent(image_base64=img_b64)],
            )
            # We use `send_message` (non-streaming) because OCR is a
            # single-shot request-response — streaming buys us nothing
            # and adds an event-loop coupling we don't need. This is
            # the explicit non-streaming case the playbook allows.
            result = asyncio.run(chat.send_message(msg))
            if isinstance(result, str):
                return result.strip()
            # Some emergentintegrations versions return an object with
            # .content — handle both shapes defensively.
            return (getattr(result, "content", "") or "").strip()
        except RuntimeError:
            # `asyncio.run` fails when we're already inside a running
            # loop. Fall back to nesting via a new loop in a thread —
            # this path is used only when `transcribe_bytes` is invoked
            # from an already-async context by mistake. The primary
            # call site (extractor) runs in a worker thread so this
            # branch is normally not hit.
            try:
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(self._atranscribe_impl(png_bytes))
                finally:
                    loop.close()
            except Exception:
                logger.exception("gpt-4o-mini vision OCR nested-loop failed")
                return ""
        except Exception:
            logger.exception("gpt-4o-mini vision OCR failed")
            return ""

    async def _atranscribe_impl(self, png_bytes: bytes) -> str:
        from emergentintegrations.llm.chat import (
            LlmChat, UserMessage, ImageContent,
        )
        img_b64 = self._downscale_and_encode(png_bytes)
        chat = LlmChat(
            api_key=self._api_key,
            session_id=f"studio-ocr-{os.urandom(6).hex()}",
            system_message=self._config.system_message,
        ).with_model(self._config.model_provider, self._config.model_name)
        msg = UserMessage(
            text=self._config.prompt,
            file_contents=[ImageContent(image_base64=img_b64)],
        )
        result = await chat.send_message(msg)
        if isinstance(result, str):
            return result.strip()
        return (getattr(result, "content", "") or "").strip()


# ---------------------------------------------------------------------------
# Provider chain (public entry point)
# ---------------------------------------------------------------------------

class OCRProviderChain:
    """Try each provider in order; return the first non-empty result.

    Also records which provider was used so we can persist that on the
    extracted record."""

    def __init__(self, providers: list[OCRProvider], min_useful_chars: int = 6) -> None:
        self._providers = providers
        self._min_useful_chars = min_useful_chars

    @property
    def available_providers(self) -> list[str]:
        return [p.name for p in self._providers if p.is_available()]

    def transcribe(self, png_bytes: bytes) -> Tuple[str, str]:
        """Return `(text, provider_used)`. `provider_used == "none"` if
        every provider was unavailable or returned empty."""
        for p in self._providers:
            if not p.is_available():
                continue
            try:
                text = p.transcribe_bytes(png_bytes)
            except Exception:
                logger.exception("OCR provider %s crashed", p.name)
                continue
            if text and len(text) >= self._min_useful_chars:
                return text, p.name
            # empty / near-empty → try the next provider
        return "", "none"

    async def atranscribe(self, png_bytes: bytes) -> Tuple[str, str]:
        return await asyncio.to_thread(self.transcribe, png_bytes)


# ---------------------------------------------------------------------------
# Module-level singleton (cheap to build; providers cache their own state)
# ---------------------------------------------------------------------------

_chain: Optional[OCRProviderChain] = None


def get_ocr_provider_chain() -> OCRProviderChain:
    global _chain
    if _chain is None:
        _chain = OCRProviderChain([
            TesseractProvider(),          # local, fast, may be absent in prod
            GPT4oMiniVisionProvider(),    # cloud, persistent, always available in prod
        ])
    return _chain


def reset_ocr_provider_chain() -> None:
    """Testing hook — clear the cached chain so tests can inject
    fake providers via monkeypatch."""
    global _chain
    _chain = None
