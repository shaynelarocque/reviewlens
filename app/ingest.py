"""Ingestion module: CSV parsing and Firecrawl URL scraping."""

from __future__ import annotations

import csv
import io
import os
import re
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

import httpx

from .models import IngestionSummary, Review


# ── CSV Ingestion (primary path) ────────────────────────────────────

# Common column name variants we normalise to our schema.
_COL_MAP: dict[str, list[str]] = {
    "text": ["text", "review", "review_text", "content", "body", "comment", "review_body", "reviews", "feedback"],
    "rating": ["rating", "score", "stars", "star_rating", "review_rating", "overall_rating"],
    "date": ["date", "review_date", "created_at", "timestamp", "time", "posted_date", "review_time"],
    "author": ["author", "reviewer", "user", "username", "reviewer_name", "name", "user_name"],
    "platform": ["platform", "source", "site", "channel"],
}


def _normalise_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower().strip())


def _map_columns(headers: list[str]) -> dict[str, str]:
    """Map CSV column names → our field names. Returns {our_field: csv_col}."""
    mapping: dict[str, str] = {}
    normalised = {_normalise_col(h): h for h in headers}

    for field, aliases in _COL_MAP.items():
        for alias in aliases:
            norm = _normalise_col(alias)
            if norm in normalised:
                mapping[field] = normalised[norm]
                break
    return mapping


def _parse_date(val: str) -> datetime | None:
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(val.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _parse_rating(val: str) -> float | None:
    try:
        # Handle "4/5", "4 out of 5", or plain "4.5"
        val = val.strip()
        match = re.match(r"([\d.]+)\s*(?:/|out of)\s*\d+", val)
        if match:
            return float(match.group(1))
        return float(val)
    except (ValueError, TypeError):
        return None


def parse_csv(content: str | bytes, platform: str = "", product_name: str = "") -> list[Review]:
    """Parse CSV content into Review objects. Handles flexible column names."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")  # Handle BOM

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return []

    col_map = _map_columns(list(reader.fieldnames))

    if "text" not in col_map:
        # Try: if there's only one text-like long column, use it
        for h in reader.fieldnames:
            if h not in col_map.values():
                col_map["text"] = h
                break

    if "text" not in col_map:
        return []

    reviews: list[Review] = []
    for i, row in enumerate(reader):
        text = row.get(col_map.get("text", ""), "").strip()
        if not text:
            continue

        rating_raw = row.get(col_map.get("rating", ""), "")
        date_raw = row.get(col_map.get("date", ""), "")
        author = row.get(col_map.get("author", ""), "").strip()
        plat = row.get(col_map.get("platform", ""), "").strip() or platform

        # Collect unmapped columns as metadata
        mapped_cols = set(col_map.values())
        metadata = {k: v for k, v in row.items() if k not in mapped_cols and v}

        reviews.append(
            Review(
                id=f"review_{i}",
                text=text,
                rating=_parse_rating(rating_raw),
                date=_parse_date(date_raw),
                author=author,
                platform=plat,
                metadata=metadata,
            )
        )

    return reviews


# ── Firecrawl URL Scraping (secondary, best-effort) ─────────────────

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1"


async def scrape_url(url: str, platform: str = "") -> list[Review]:
    """Best-effort URL scraping via Firecrawl. Returns whatever we can get."""
    if not FIRECRAWL_API_KEY:
        raise ValueError("FIRECRAWL_API_KEY not set. Use CSV upload instead.")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{FIRECRAWL_API_URL}/scrape",
            headers={
                "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "url": url,
                "formats": ["markdown"],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    markdown = data.get("data", {}).get("markdown", "")
    if not markdown:
        return []

    # Try to extract individual reviews from the markdown.
    # This is best-effort — review platforms have varied structures.
    reviews = _extract_reviews_from_markdown(markdown, platform, url)
    return reviews


def _extract_reviews_from_markdown(
    markdown: str, platform: str, url: str
) -> list[Review]:
    """Attempt to parse review blocks from scraped markdown."""
    reviews: list[Review] = []

    # Strategy: split on patterns that look like review boundaries.
    # Common patterns: "★★★★☆", "Rating: X/5", numbered reviews, horizontal rules
    blocks = re.split(r"\n(?:---+|\*\*\*+|#{1,3}\s)", markdown)

    for i, block in enumerate(blocks):
        block = block.strip()
        if len(block) < 20:
            continue

        # Try to extract rating
        rating = None
        star_match = re.search(r"([★]{1,5})", block)
        if star_match:
            rating = float(len(star_match.group(1)))
        else:
            rating_match = re.search(r"(\d(?:\.\d)?)\s*(?:/\s*5|out of 5|stars?)", block, re.I)
            if rating_match:
                rating = float(rating_match.group(1))

        # Try to extract date
        date = None
        date_match = re.search(
            r"(\w+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
            block,
        )
        if date_match:
            date = _parse_date(date_match.group(1))

        reviews.append(
            Review(
                id=f"scraped_{i}",
                text=block[:2000],  # Cap individual review length
                rating=rating,
                date=date,
                platform=platform or _guess_platform(url),
                metadata={"source_url": url},
            )
        )

    return reviews


def _guess_platform(url: str) -> str:
    url_lower = url.lower()
    if "amazon" in url_lower:
        return "Amazon"
    if "google" in url_lower and "maps" in url_lower:
        return "Google Maps"
    if "g2" in url_lower:
        return "G2"
    if "capterra" in url_lower:
        return "Capterra"
    if "yelp" in url_lower:
        return "Yelp"
    if "trustpilot" in url_lower:
        return "Trustpilot"
    return "Web"


# ── Summary builder ──────────────────────────────────────────────────

def build_summary(
    reviews: list[Review],
    source_type: str,
    product_name: str = "",
    platform: str = "",
) -> IngestionSummary:
    """Build an ingestion summary from parsed reviews."""
    if not reviews:
        return IngestionSummary(source_type=source_type)

    ratings = [r.rating for r in reviews if r.rating is not None]
    dates = [r.date for r in reviews if r.date is not None]

    # Rating distribution (bucket by integer star)
    dist: dict[str, int] = {}
    for r in ratings:
        bucket = str(int(round(r)))
        dist[bucket] = dist.get(bucket, 0) + 1

    # Date range
    date_range = ""
    if dates:
        earliest = min(dates).strftime("%Y-%m-%d")
        latest = max(dates).strftime("%Y-%m-%d")
        date_range = f"{earliest} to {latest}" if earliest != latest else earliest

    # Platform detection
    platforms = [r.platform for r in reviews if r.platform]
    detected_platform = platform or (Counter(platforms).most_common(1)[0][0] if platforms else "Unknown")

    return IngestionSummary(
        total_reviews=len(reviews),
        date_range=date_range,
        rating_distribution=dist,
        average_rating=round(sum(ratings) / len(ratings), 2) if ratings else None,
        platform=detected_platform,
        product_name=product_name or "Unknown Product",
        source_type=source_type,
    )
