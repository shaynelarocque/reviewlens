"""Knowledge base loader — domain reference files for the agent.

Loads markdown files from /knowledge on startup, caches them,
and provides get(name) and list_files() for tool access.
"""

from __future__ import annotations

from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
_cache: dict[str, str] = {}
_summaries: dict[str, str] = {}


def load() -> None:
    """Load all markdown files from the knowledge directory into cache."""
    _cache.clear()
    _summaries.clear()

    if not _KNOWLEDGE_DIR.is_dir():
        return

    for path in sorted(_KNOWLEDGE_DIR.glob("*.md")):
        name = path.stem
        content = path.read_text(encoding="utf-8")
        _cache[name] = content
        # First non-empty, non-heading line as summary
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                _summaries[name] = stripped[:120]
                break
        else:
            _summaries[name] = "(no summary)"


def get(name: str) -> str | None:
    """Get a knowledge file by name (stem, without .md extension).

    Supports exact match and fuzzy prefix match.
    """
    if name in _cache:
        return _cache[name]
    # Try prefix match
    for key in _cache:
        if key.startswith(name) or name in key:
            return _cache[key]
    return None


def list_files() -> list[dict[str, str]]:
    """List available knowledge files with summaries."""
    return [
        {"name": name, "summary": _summaries.get(name, "")}
        for name in sorted(_cache.keys())
    ]
