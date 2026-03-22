"""Shared helpers for MCP tools — emit, source collection, text analysis."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Awaitable

# Type alias for the SSE emit callback
EmitFn = Callable[[str, str, str], Awaitable[None]]

# Type alias for the bound emit_tool helper
EmitToolFn = Callable[..., Awaitable[None]]

# Type alias for the bound collect_sources helper
CollectSourcesFn = Callable[[list[dict[str, Any]]], None]


def make_emit_tool(
    session_id: str,
    emit_fn: EmitFn,
    tool_records: list[dict] | None,
    timeline: list[dict] | None = None,
) -> EmitToolFn:
    """Create a bound _emit_tool helper that closes over session state."""

    async def emit_tool(
        tool_name: str,
        summary: str,
        inputs: dict[str, Any],
        output_summary: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "tool_name": tool_name,
            "summary": summary,
            "inputs": inputs,
            "output_summary": output_summary or {},
        }
        if tool_records is not None:
            tool_records.append(record)
        if timeline is not None:
            timeline.append({"type": "tool", **record})
        payload = json.dumps(record)
        await emit_fn(session_id, payload, "tool")

    return emit_tool


def make_collect_sources(
    cited_sources: list[dict] | None,
    seen_source_ids: set[str],
) -> CollectSourcesFn:
    """Create a bound _collect_sources helper that deduplicates sources."""

    def collect_sources(results: list[dict[str, Any]]) -> None:
        if cited_sources is None:
            return
        for r in results:
            rid = r.get("id", "")
            if rid and rid not in seen_source_ids:
                seen_source_ids.add(rid)
                cited_sources.append({
                    "id": rid,
                    "text": r.get("text", "")[:500],
                    "rating": r.get("metadata", {}).get("rating"),
                    "date": r.get("metadata", {}).get("date"),
                    "author": r.get("metadata", {}).get("author", ""),
                })

    return collect_sources


# ── Text analysis ────────────────────────────────────────────────────

STOPWORDS = frozenset((
    "the", "a", "an", "is", "was", "are", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "to", "of",
    "in", "for", "on", "with", "at", "by", "from", "as", "into", "through",
    "during", "before", "after", "above", "below", "between", "out", "off",
    "over", "under", "again", "further", "then", "once", "here", "there",
    "when", "where", "why", "how", "all", "each", "every", "both", "few",
    "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "just", "because", "but",
    "and", "or", "if", "while", "about", "up", "down", "also", "still",
    "it", "its", "this", "that", "these", "those", "i", "me", "my", "we",
    "our", "you", "your", "he", "him", "his", "she", "her", "they", "them",
    "their", "what", "which", "who", "whom", "get", "got", "really", "like",
    "even", "much", "well", "back", "going", "went", "come", "came",
    "make", "made", "one", "two", "first", "new", "way", "thing", "things",
    "know", "take", "see", "think", "say", "said", "time", "been", "ive",
    "dont", "didnt", "wont", "cant", "im", "ive", "thats", "its",
    # Review-specific noise
    "product", "review", "bought", "ordered", "purchase", "purchased",
    "item", "received", "use", "used", "using", "would", "recommend",
    "star", "stars", "rating", "overall", "experience",
))


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase content words, filtering stopwords."""
    words = re.findall(r'[a-z]+', text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]
