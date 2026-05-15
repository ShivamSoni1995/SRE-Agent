"""
embeddings.py — Gemini embedding API client with in-memory caching.

Uses text-embedding-004 (free tier, 768 dimensions).
Falls back gracefully when GEMINI_API_KEY is not set.
Caches embeddings by text hash to avoid redundant API calls.
"""
import os
import hashlib
import logging
import math
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GEMINI_EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "text-embedding-004:embedContent"
)

# In-memory LRU-style cache: text_hash -> embedding vector
_cache: dict[str, list[float]] = {}
_MAX_CACHE = 512


async def embed(text: str) -> Optional[list[float]]:
    """
    Return a 768-dim embedding for the given text.
    Returns None if the API key is missing or the call fails.
    """
    # Lazy import to avoid circular dependency at module load
    try:
        from app import metrics as m
        _metrics_available = True
    except Exception:
        _metrics_available = False

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return None

    cache_key = hashlib.sha256(text.encode()).hexdigest()

    if cache_key in _cache:
        logger.debug("Embedding cache hit")
        if _metrics_available:
            m.embedding_api_calls_total.labels(outcome="cache_hit").inc()
        return _cache[cache_key]

    payload = {
        "model": "models/text-embedding-004",
        "content": {"parts": [{"text": text}]},
        "taskType": "SEMANTIC_SIMILARITY",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{GEMINI_EMBED_URL}?key={api_key}",
                json=payload,
            )
            resp.raise_for_status()
            vector = resp.json()["embedding"]["values"]

        # Evict oldest entry if cache is full
        if len(_cache) >= _MAX_CACHE:
            oldest = next(iter(_cache))
            del _cache[oldest]

        _cache[cache_key] = vector

        if _metrics_available:
            m.embedding_api_calls_total.labels(outcome="success").inc()
            m.embedding_cache_size.set(len(_cache))

        logger.debug(f"Embedded ({len(text)} chars) → {len(vector)}-dim vector")
        return vector

    except Exception as e:
        logger.warning(f"Embedding API failed: {e}")
        if _metrics_available:
            m.embedding_api_calls_total.labels(outcome="error").inc()
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns value in [-1, 1]."""
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def cache_stats() -> dict:
    return {"size": len(_cache), "max": _MAX_CACHE}
