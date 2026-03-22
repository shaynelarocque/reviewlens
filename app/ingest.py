"""Ingestion module: CSV parsing and Firecrawl URL scraping."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

import anthropic
import httpx

from .models import IngestionSummary, Review

log = logging.getLogger(__name__)


# ── CSV Ingestion (primary path) ────────────────────────────────────

# Static alias map — used as fallback when the AI mapping fails.
_COL_MAP: dict[str, list[str]] = {
    "text": ["text", "review", "review_text", "content", "body", "comment", "review_body", "reviews", "feedback"],
    "rating": ["rating", "score", "stars", "star_rating", "review_rating", "overall_rating"],
    "date": ["date", "review_date", "created_at", "timestamp", "time", "posted_date", "review_time"],
    "author": ["author", "reviewer", "user", "username", "reviewer_name", "name", "user_name"],
    "platform": ["platform", "source", "site", "channel"],
}


def _normalise_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower().strip())


def _map_columns_static(headers: list[str]) -> dict[str, str]:
    """Static alias-based column mapping. Returns {our_field: csv_col}."""
    mapping: dict[str, str] = {}
    normalised = {_normalise_col(h): h for h in headers}

    for field, aliases in _COL_MAP.items():
        for alias in aliases:
            norm = _normalise_col(alias)
            if norm in normalised:
                mapping[field] = normalised[norm]
                break
    return mapping


# ── AI Column Mapping ────────────────────────────────────────────────

_COLUMN_MAP_TOOL = {
    "name": "map_columns",
    "description": "Map CSV columns to canonical review fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The CSV column containing the primary review text / body.",
            },
            "rating": {
                "type": ["string", "null"],
                "description": "The CSV column containing the star rating or score. Null if none.",
            },
            "date": {
                "type": ["string", "null"],
                "description": "The CSV column containing the review date. Null if none.",
            },
            "author": {
                "type": ["string", "null"],
                "description": "The CSV column containing the reviewer's name. Null if none. Do NOT map role/title columns here.",
            },
            "platform": {
                "type": ["string", "null"],
                "description": "The CSV column containing the platform/source name. Null if none.",
            },
            "concat_into_text": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Other CSV columns whose values should be prepended/appended to the review text to make it complete. E.g. title, pros, cons, summary. Order matters — they will be joined in this order before the main text.",
            },
        },
        "required": ["text", "rating", "date", "author", "platform", "concat_into_text"],
    },
}


async def _map_columns_ai(headers: list[str], sample_rows: list[dict]) -> dict[str, Any] | None:
    """Use Claude to map CSV columns to our canonical fields. Returns mapping or None on failure."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    # Build a concise preview of the data
    preview_lines = [",".join(headers)]
    for row in sample_rows[:3]:
        vals = []
        for h in headers:
            v = str(row.get(h, ""))
            vals.append(v[:120] + "..." if len(v) > 120 else v)
        preview_lines.append(",".join(vals))
    preview = "\n".join(preview_lines)

    prompt = f"""You are mapping CSV columns to a review database schema.

Here are the CSV headers and first few rows:

{preview}

Map each CSV column to one of these canonical fields:
- text: the primary review body/content
- rating: numeric star rating or score
- date: when the review was posted
- author: the reviewer's display name (NOT their role, title, or company)
- platform: the review source/platform name

Also identify columns that contain supplementary text that should be concatenated into the main review text to make it more complete and useful for semantic search. Common examples:
- "title" or "review_title" → prepend to the review text
- "pros" / "cons" → append as "Pros: ... Cons: ..."
- "summary" → prepend

Do NOT include ID columns, metadata like company_size, verified_purchase, or owner responses in concat_into_text.

Use the map_columns tool to return your mapping."""

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            tools=[_COLUMN_MAP_TOOL],
            messages=[{"role": "user", "content": prompt}],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "map_columns":
                return block.input
        return None

    except Exception as e:
        log.warning("AI column mapping failed: %s", e)
        return None


def _ai_result_to_col_map(ai_result: dict[str, Any], headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Convert AI mapping result to {our_field: csv_col} + concat list.
    Validates that all referenced columns actually exist in the CSV."""
    header_set = set(headers)
    col_map: dict[str, str] = {}
    concat_cols: list[str] = []

    for field in ("text", "rating", "date", "author", "platform"):
        val = ai_result.get(field)
        if val and val in header_set:
            col_map[field] = val

    for col in ai_result.get("concat_into_text", []):
        if col in header_set and col != col_map.get("text"):
            concat_cols.append(col)

    return col_map, concat_cols


# ── Date/Rating parsers ──────────────────────────────────────────────

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
        val = val.strip()
        match = re.match(r"([\d.]+)\s*(?:/|out of)\s*\d+", val)
        if match:
            return float(match.group(1))
        return float(val)
    except (ValueError, TypeError):
        return None


# ── CSV Parser ───────────────────────────────────────────────────────

async def parse_csv(content: str | bytes, platform: str = "", product_name: str = "") -> list[Review]:
    """Parse CSV content into Review objects. Uses AI for column mapping with static fallback."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return []

    headers = list(reader.fieldnames)

    # Read all rows upfront (we need sample rows for AI + full iteration)
    all_rows = list(reader)
    if not all_rows:
        return []

    # Try AI column mapping first
    concat_cols: list[str] = []
    ai_result = await _map_columns_ai(headers, all_rows[:3])

    if ai_result and ai_result.get("text"):
        col_map, concat_cols = _ai_result_to_col_map(ai_result, headers)
        log.info("AI column mapping: %s, concat: %s", col_map, concat_cols)
    else:
        col_map = _map_columns_static(headers)
        log.info("Static column mapping (AI unavailable): %s", col_map)

    if "text" not in col_map:
        # Last resort: pick first unmapped column
        for h in headers:
            if h not in col_map.values():
                col_map["text"] = h
                break

    if "text" not in col_map:
        return []

    reviews: list[Review] = []
    for i, row in enumerate(all_rows):
        text = row.get(col_map.get("text", ""), "").strip()
        if not text:
            continue

        # Concatenate supplementary text columns
        if concat_cols:
            parts = []
            for col in concat_cols:
                val = row.get(col, "").strip()
                if val:
                    # Use the column name as a label for clarity
                    label = col.replace("_", " ").title()
                    parts.append(f"{label}: {val}")
            if parts:
                text = "\n".join(parts) + "\n\n" + text

        rating_raw = row.get(col_map.get("rating", ""), "")
        date_raw = row.get(col_map.get("date", ""), "")
        author = row.get(col_map.get("author", ""), "").strip()
        plat = row.get(col_map.get("platform", ""), "").strip() or platform

        # Collect unmapped columns as metadata
        mapped_cols = set(col_map.values()) | set(concat_cols)
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
# Uses the /v2/agent endpoint which autonomously navigates pagination
# and returns structured data — no brittle markdown regex parsing.

FIRECRAWL_API_URL = "https://api.firecrawl.dev/v2"

_AGENT_POLL_INTERVAL = 2   # seconds between status checks
_AGENT_TIMEOUT = 300       # total seconds before giving up
_AGENT_MAX_CREDITS = 250   # stay well under the 500/run free-tier limit

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Full review text written by the customer"},
                    "rating": {"type": ["number", "null"], "description": "Star rating from 1 to 5"},
                    "date": {"type": ["string", "null"], "description": "Date posted in YYYY-MM-DD format"},
                    "author": {"type": ["string", "null"], "description": "Reviewer's display name"},
                },
                "required": ["text"],
            },
        }
    },
    "required": ["reviews"],
}


def _build_agent_prompt(url: str, platform: str) -> str:
    platform_hint = f"This is a {platform} review page. " if platform else ""
    return (
        f"{platform_hint}"
        f"Extract customer/user reviews from {url}. "
        "For each review, extract: "
        "(1) the COMPLETE review text — the full body/content of what the reviewer "
        "wrote, not just the title or summary. Include both the review title and the "
        "full paragraph(s) of text. On G2 and similar sites, you may need to expand "
        "'Read more' or similar elements to get the full text. "
        "(2) the star rating as a number from 1 to 5 (if shown), "
        "(3) the date it was posted in YYYY-MM-DD format (if shown), "
        "(4) the reviewer's name (if shown). "
        "Only extract actual user-submitted reviews — not editorial content, product "
        "descriptions, or page navigation text. "
        "IMPORTANT: Only scrape the first page of reviews. Do NOT click through to "
        "additional pages or follow pagination links. Extract what is visible on the "
        "initial page load only."
    )


async def scrape_url(url: str, platform: str = "") -> list[Review]:
    """Best-effort URL scraping via Firecrawl agent. Returns structured reviews."""
    api_key = os.getenv("FIRECRAWL_API_KEY", "")
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY not set. Use CSV upload instead.")

    detected_platform = platform or _guess_platform(url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Submit agent job
        resp = await client.post(
            f"{FIRECRAWL_API_URL}/agent",
            headers=headers,
            json={
                "urls": [url],
                "prompt": _build_agent_prompt(url, detected_platform),
                "schema": _REVIEW_SCHEMA,
                "model": "spark-1-mini",
                "maxCredits": _AGENT_MAX_CREDITS,
            },
        )
        resp.raise_for_status()
        job = resp.json()

        job_id = job.get("id")
        if not job_id:
            return []

        # Poll for completion — tolerate transient 5xx errors
        elapsed = 0.0
        while elapsed < _AGENT_TIMEOUT:
            await asyncio.sleep(_AGENT_POLL_INTERVAL)
            elapsed += _AGENT_POLL_INTERVAL

            try:
                poll = await client.get(
                    f"{FIRECRAWL_API_URL}/agent/{job_id}",
                    headers=headers,
                )
                if poll.status_code >= 500:
                    continue  # Retry on transient server errors
                poll.raise_for_status()
                result = poll.json()
            except httpx.HTTPStatusError:
                continue  # Retry
            except httpx.RequestError:
                continue  # Network blip, retry

            status = result.get("status", "")
            if status == "completed":
                return _parse_agent_reviews(result.get("data", {}), detected_platform, url)
            if status in ("failed", "cancelled"):
                return []

    # Timed out
    return []


def _parse_agent_reviews(data: dict, platform: str, url: str) -> list[Review]:
    """Convert the agent's structured output into Review objects."""
    raw_reviews = data.get("reviews", [])
    if not isinstance(raw_reviews, list):
        return []

    reviews: list[Review] = []
    for i, item in enumerate(raw_reviews):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue

        # Normalise rating — agent may return int, float, string, or null
        rating = None
        raw_rating = item.get("rating")
        if raw_rating is not None:
            rating = _parse_rating(str(raw_rating))

        # Normalise date — agent should return YYYY-MM-DD but be defensive
        date = None
        raw_date = item.get("date")
        if raw_date:
            date = _parse_date(str(raw_date))

        author = str(item.get("author", "") or "").strip()

        reviews.append(
            Review(
                id=f"scraped_{i}",
                text=text[:2000],
                rating=rating,
                date=date,
                author=author,
                platform=platform,
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
