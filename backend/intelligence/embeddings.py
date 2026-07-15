"""Model-agnostic embedding provider.

The retrieval engine only sees the EmbeddingProvider interface — swapping
BGE for OpenAI/SigLIP/anything later means adding one class + one env var,
nothing else changes.

Default provider: local BGE-small (ONNX via fastembed). Zero recurring
cost, ~10ms per text on CPU, 384-d. The Emergent LLM key exposes no
embedding models, so local is both the cheap and the only API-free option.
"""
from __future__ import annotations

import logging
import math
import os
import threading

logger = logging.getLogger(__name__)


class LocalBGEEmbedder:
    name = "bge-small-en-v1.5"
    dims = 384

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding
                    logger.info("embeddings: loading %s (one-time)", self.name)
                    self._model = TextEmbedding("BAAI/bge-small-en-v1.5")
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure()
        return [[float(x) for x in v] for v in model.embed(texts)]


_PROVIDERS = {"local-bge": LocalBGEEmbedder}
_instance = None
_instance_lock = threading.Lock()


def get_embedder():
    """Singleton embedding provider selected via EMBEDDING_PROVIDER env."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                key = os.environ.get("EMBEDDING_PROVIDER", "local-bge")
                cls = _PROVIDERS.get(key, LocalBGEEmbedder)
                _instance = cls()
    return _instance


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
