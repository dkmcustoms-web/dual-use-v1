"""OpenAI embeddings service.

Uses text-embedding-3-small (1536 dim, $0.02/MTok, multilingual).
Provides batched embed-many for bulk indexing and embed-one for queries.

Cost model: ~$0.000002 per typical entry (500 chars ≈ 125 tokens).
Bulk embed of full corpus (~3000 entries) ≈ $0.012.
Per-query cost ≈ $0.0000002 — effectively free.
"""
from __future__ import annotations

import os
import time
from typing import Iterable

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
EMBEDDING_PRICE_USD_PER_MTOK = 0.02

# OpenAI accepts up to 2048 inputs per request; we use 200 to keep
# request sizes manageable and avoid timeouts on Railway.
DEFAULT_BATCH_SIZE = 200


_client = None


def get_client():
    """Lazy OpenAI client init — raises if key is missing."""
    global _client
    if _client is not None:
        return _client
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to .env locally or to "
            "Railway → Variables."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "openai package not installed. Add 'openai' to requirements.txt."
        ) from exc
    _client = OpenAI()
    return _client


def embed_texts(texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
    """Embed a list of texts. Returns dict with:
        embeddings:    list of 1536-dim vectors (same order as input)
        total_tokens:  total input tokens consumed
        cost_usd:      estimated cost in USD
        elapsed_sec:   total wall time
    """
    if not texts:
        return {"embeddings": [], "total_tokens": 0, "cost_usd": 0.0, "elapsed_sec": 0.0}

    client = get_client()
    all_vectors: list[list[float]] = []
    total_tokens = 0
    t0 = time.time()

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        # OpenAI fails on empty strings; replace with a single space
        batch = [t if t and t.strip() else " " for t in batch]
        resp = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=batch,
        )
        # data is ordered by index in the request
        all_vectors.extend(d.embedding for d in resp.data)
        total_tokens += resp.usage.total_tokens

    elapsed = time.time() - t0
    cost_usd = total_tokens / 1_000_000 * EMBEDDING_PRICE_USD_PER_MTOK
    return {
        "embeddings": all_vectors,
        "total_tokens": total_tokens,
        "cost_usd": round(cost_usd, 6),
        "elapsed_sec": round(elapsed, 2),
    }


def embed_query(query: str) -> list[float]:
    """Embed a single query string."""
    result = embed_texts([query])
    return result["embeddings"][0] if result["embeddings"] else []


def to_pg_vector_literal(vec: list[float]) -> str:
    """Convert a Python float list to a pgvector text literal '[v1,v2,...]'.

    Passed as a text parameter to pgvector, which parses it. Avoids
    needing the pgvector-python SQLAlchemy adapter.
    """
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def estimate_cost_for_corpus(num_entries: int, avg_chars_per_entry: int = 500) -> dict:
    """Estimate the cost of embedding a corpus before actually doing it."""
    # ~4 chars per token (English/Dutch heuristic)
    estimated_tokens = num_entries * avg_chars_per_entry // 4
    cost_usd = estimated_tokens / 1_000_000 * EMBEDDING_PRICE_USD_PER_MTOK
    return {
        "estimated_tokens": estimated_tokens,
        "estimated_cost_usd": round(cost_usd, 4),
    }
