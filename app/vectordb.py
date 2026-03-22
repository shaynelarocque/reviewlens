"""ChromaDB vector store for review embeddings."""

from __future__ import annotations

import hashlib
import os
from typing import Any

import chromadb
from chromadb.config import Settings

from .models import Review

# In-process persistent ChromaDB — no external server needed.
_client: chromadb.ClientAPI | None = None


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        persist_dir = os.getenv("CHROMA_DIR", "data/chroma")
        _client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def _collection_name(session_id: str) -> str:
    """ChromaDB collection names must be 3-63 chars, alphanumeric + underscores."""
    h = hashlib.md5(session_id.encode()).hexdigest()[:12]
    return f"reviews_{h}"


def index_reviews(session_id: str, reviews: list[Review]) -> int:
    """Embed and store reviews. Returns count indexed."""
    client = _get_client()
    col = client.get_or_create_collection(
        name=_collection_name(session_id),
        metadata={"hnsw:space": "cosine"},
    )

    if not reviews:
        return 0

    ids = []
    documents = []
    metadatas = []

    for i, r in enumerate(reviews):
        if not r.text.strip():
            continue
        doc_id = r.id or f"review_{i}"
        ids.append(doc_id)
        documents.append(r.text)
        meta: dict[str, Any] = {}
        if r.rating is not None:
            meta["rating"] = float(r.rating)
        if r.date:
            meta["date"] = r.date.isoformat()
        if r.author:
            meta["author"] = r.author
        if r.platform:
            meta["platform"] = r.platform
        metadatas.append(meta)

    # ChromaDB has a batch limit — upsert in chunks of 500.
    batch_size = 500
    for start in range(0, len(ids), batch_size):
        end = start + batch_size
        col.upsert(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )

    return len(ids)


def search_reviews(
    session_id: str,
    query: str,
    n_results: int = 10,
    where: dict | None = None,
) -> list[dict[str, Any]]:
    """Semantic search over indexed reviews. Returns list of result dicts."""
    client = _get_client()
    col_name = _collection_name(session_id)

    try:
        col = client.get_collection(col_name)
    except Exception:
        return []

    kwargs: dict[str, Any] = {
        "query_texts": [query],
        "n_results": min(n_results, col.count() or 1),
    }
    if where:
        kwargs["where"] = where

    results = col.query(**kwargs)

    out = []
    for i in range(len(results["ids"][0])):
        out.append(
            {
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "distance": results["distances"][0][i] if results.get("distances") else None,
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
            }
        )
    return out


def get_all_reviews(session_id: str) -> list[dict[str, Any]]:
    """Retrieve all reviews from the collection (for stats/analysis)."""
    client = _get_client()
    col_name = _collection_name(session_id)

    try:
        col = client.get_collection(col_name)
    except Exception:
        return []

    count = col.count()
    if count == 0:
        return []

    results = col.get(include=["documents", "metadatas"])

    out = []
    for i in range(len(results["ids"])):
        out.append(
            {
                "id": results["ids"][i],
                "text": results["documents"][i],
                "metadata": results["metadatas"][i] if results.get("metadatas") else {},
            }
        )
    return out


def get_review_by_id(session_id: str, review_id: str) -> dict[str, Any] | None:
    """Get a single review by ID. Returns None if not found."""
    client = _get_client()
    try:
        col = client.get_collection(_collection_name(session_id))
    except Exception:
        return None

    results = col.get(ids=[review_id], include=["documents", "metadatas"])
    if not results["ids"]:
        return None

    return {
        "id": results["ids"][0],
        "text": results["documents"][0],
        "metadata": results["metadatas"][0] if results.get("metadatas") else {},
    }


def get_review_count(session_id: str) -> int:
    client = _get_client()
    try:
        col = client.get_collection(_collection_name(session_id))
        return col.count()
    except Exception:
        return 0
