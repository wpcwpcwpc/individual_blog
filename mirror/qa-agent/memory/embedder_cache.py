"""
QA Agent System — Embedding Cache Wrapper

Wraps an agno Embedder (e.g. ``OpenAIEmbedder``) with a thread-safe
LRU cache on ``get_embedding`` and ``get_embedding_and_usage``.

Embeddings are deterministic for a given (model, text) pair — same
input always produces the same vector.  Caching avoids redundant API
calls when the Knowledge Hub searches multiple collections with the
same query text (which ``hub_retriever`` does 3× per search — once
per collection).
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _make_cache_key(text: str) -> int:
    return hash(text)


@functools.lru_cache(maxsize=512)
def _cached_get_embedding(text: str, dimensions: int, encoding_format: str) -> List[float]:
    """Stub — replaced at module level after the embedder is wrapped."""
    raise RuntimeError("EmbedderCache not initialized")


class EmbedderCache:
    """Decorates an agno-compatible embedder with an LRU cache."""

    def __init__(self, embedder: Any, maxsize: int = 512):
        self._embedder = embedder
        self._id = getattr(embedder, "id", "unknown")
        self._dimensions = getattr(embedder, "dimensions", 1536)
        self._encoding_format = getattr(embedder, "encoding_format", "float")

        self._stats: Dict[str, int] = {"hits": 0, "misses": 0}

        # Build a module-level cached function bound to this embedder.
        # lru_cache on a closure captures the underlying embedder ref.
        @functools.lru_cache(maxsize=maxsize)
        def _get_embedding_cached(text: str, dimensions: int, encoding_format: str) -> List[float]:
            # Type-stable sentinel; first arg matches cached signature.
            return self._embedder.get_embedding(text)

        self._get_embedding_cached = _get_embedding_cached

    def __getattr__(self, name: str) -> Any:
        """Forward all other attribute access to the underlying embedder."""
        return getattr(self._embedder, name)

    @property
    def id(self) -> str:
        return self._id

    @property
    def dimensions(self) -> Optional[int]:
        return self._dimensions

    @property
    def encoding_format(self) -> str:
        return self._encoding_format

    def get_embedding(self, text: str) -> List[float]:
        info_before = self._get_embedding_cached.cache_info()
        result = self._get_embedding_cached(text, self._dimensions, self._encoding_format)
        info_after = self._get_embedding_cached.cache_info()

        if info_after.hits > info_before.hits:
            logger.debug("EmbedderCache HIT: %.80r", text)
        else:
            logger.debug("EmbedderCache MISS: %.80r", text)

        logger.debug(
            "EmbedderCache stats: hits=%d misses=%d currsize=%d",
            info_after.hits, info_after.misses, info_after.currsize,
        )
        return result

    def get_embedding_and_usage(self, text: str) -> Tuple[List[float], Optional[Dict]]:
        embedding = self.get_embedding(text)
        return embedding, None

    def cache_stats(self) -> Dict[str, int]:
        info = self._get_embedding_cached.cache_info()
        return {
            "hits": info.hits,
            "misses": info.misses,
            "currsize": info.currsize,
            "maxsize": info.maxsize,
        }

    def reset_cache(self) -> None:
        self._get_embedding_cached.cache_clear()
        logger.info("EmbedderCache reset")
