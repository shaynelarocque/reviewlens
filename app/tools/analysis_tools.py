"""Analysis tools — segment comparison, theme extraction, anomaly detection."""

from __future__ import annotations

import json
import re
from typing import Any

from claude_agent_sdk import tool

from .. import vectordb
from ._helpers import EmitToolFn, CollectSourcesFn, tokenize


def create_analysis_tools(
    session_id: str,
    emit_tool: EmitToolFn,
    collect_sources: CollectSourcesFn,
) -> list:
    """Return analysis tool definitions."""

    # ── compare_segments ─────────────────────────────────────────────

    @tool(
        name="compare_segments",
        description="Compare two segments of reviews side by side — e.g. 5-star vs 1-star, recent vs older, topic A vs topic B. Returns structured comparison with counts, avg ratings, unique terms, and sample reviews per segment.",
        input_schema={
            "type": "object",
            "properties": {
                "segment_a": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Human label, e.g. 'Positive reviews'"},
                        "query": {"type": "string", "description": "Optional semantic search query"},
                        "min_rating": {"type": "number"},
                        "max_rating": {"type": "number"},
                        "date_after": {"type": "string", "description": "ISO date"},
                        "date_before": {"type": "string", "description": "ISO date"},
                    },
                    "required": ["label"],
                },
                "segment_b": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "query": {"type": "string"},
                        "min_rating": {"type": "number"},
                        "max_rating": {"type": "number"},
                        "date_after": {"type": "string"},
                        "date_before": {"type": "string"},
                    },
                    "required": ["label"],
                },
            },
            "required": ["segment_a", "segment_b"],
        },
    )
    async def compare_segments_tool(args: dict[str, Any]) -> dict[str, Any]:

        def _filter_reviews(seg: dict, all_reviews: list[dict]) -> list[dict]:
            if seg.get("query"):
                conditions = []
                if "min_rating" in seg:
                    conditions.append({"rating": {"$gte": seg["min_rating"]}})
                if "max_rating" in seg:
                    conditions.append({"rating": {"$lte": seg["max_rating"]}})
                if "date_after" in seg:
                    conditions.append({"date": {"$gte": seg["date_after"]}})
                if "date_before" in seg:
                    conditions.append({"date": {"$lte": seg["date_before"]}})
                where = None
                if len(conditions) > 1:
                    where = {"$and": conditions}
                elif len(conditions) == 1:
                    where = conditions[0]
                return vectordb.search_reviews(session_id, seg["query"], n_results=50, where=where)
            else:
                out = []
                for r in all_reviews:
                    meta = r.get("metadata", {})
                    rating = meta.get("rating")
                    date = meta.get("date", "")
                    if "min_rating" in seg and (rating is None or rating < seg["min_rating"]):
                        continue
                    if "max_rating" in seg and (rating is None or rating > seg["max_rating"]):
                        continue
                    if "date_after" in seg and (not date or date < seg["date_after"]):
                        continue
                    if "date_before" in seg and (not date or date > seg["date_before"]):
                        continue
                    out.append(r)
                return out

        def _top_terms(reviews: list[dict], n: int = 15) -> list[tuple[str, int]]:
            freq: dict[str, int] = {}
            for r in reviews:
                words = tokenize(r.get("text", ""))
                for i in range(len(words) - 1):
                    bg = f"{words[i]} {words[i+1]}"
                    freq[bg] = freq.get(bg, 0) + 1
                for w in words:
                    if len(w) >= 4:
                        freq[w] = freq.get(w, 0) + 1
            return sorted(freq.items(), key=lambda x: -x[1])[:n]

        all_reviews = vectordb.get_all_reviews(session_id)
        seg_a = args["segment_a"]
        seg_b = args["segment_b"]

        reviews_a = _filter_reviews(seg_a, all_reviews)
        reviews_b = _filter_reviews(seg_b, all_reviews)

        collect_sources(reviews_a[:10])
        collect_sources(reviews_b[:10])

        def _segment_stats(reviews, label):
            ratings = [r.get("metadata", {}).get("rating") for r in reviews
                       if r.get("metadata", {}).get("rating") is not None]
            terms = _top_terms(reviews)
            samples = [{"id": r["id"], "text": r["text"][:300],
                         "rating": r.get("metadata", {}).get("rating")}
                       for r in reviews[:5]]
            return {
                "label": label,
                "count": len(reviews),
                "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "top_terms": [{"term": t, "count": c} for t, c in terms[:10]],
                "sample_reviews": samples,
            }

        result_a = _segment_stats(reviews_a, seg_a["label"])
        result_b = _segment_stats(reviews_b, seg_b["label"])

        terms_a = {t for t, _ in _top_terms(reviews_a, 20)}
        terms_b = {t for t, _ in _top_terms(reviews_b, 20)}

        await emit_tool(
            "compare_segments",
            f"Compared: \"{seg_a['label']}\" ({len(reviews_a)}) vs \"{seg_b['label']}\" ({len(reviews_b)})",
            {"segment_a": seg_a["label"], "segment_b": seg_b["label"]},
            {"count_a": len(reviews_a), "count_b": len(reviews_b)},
        )

        return {"content": [{"type": "text", "text": json.dumps({
            "segment_a": result_a,
            "segment_b": result_b,
            "unique_to_a": list(terms_a - terms_b)[:8],
            "unique_to_b": list(terms_b - terms_a)[:8],
            "shared_terms": list(terms_a & terms_b)[:8],
        })}]}

    # ── extract_themes ───────────────────────────────────────────────

    @tool(
        name="extract_themes",
        description="Discover and rank themes/topics across the review corpus using n-gram frequency analysis. Goes beyond keyword search by analysing a broad slice of the dataset. Use for 'what are people talking about?' questions.",
        input_schema={
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "Optional focus area — e.g. 'complaints', 'praise', 'feature requests'. Leave empty for general theme extraction.",
                },
                "min_rating": {"type": "number"},
                "max_rating": {"type": "number"},
                "max_reviews": {
                    "type": "integer",
                    "description": "Max reviews to analyse. Default 50, max 100.",
                    "default": 50,
                },
            },
        },
    )
    async def extract_themes_tool(args: dict[str, Any]) -> dict[str, Any]:
        max_reviews = min(args.get("max_reviews", 50), 100)
        focus = args.get("focus", "")

        if focus:
            conditions = []
            if "min_rating" in args:
                conditions.append({"rating": {"$gte": args["min_rating"]}})
            if "max_rating" in args:
                conditions.append({"rating": {"$lte": args["max_rating"]}})
            where = None
            if len(conditions) > 1:
                where = {"$and": conditions}
            elif len(conditions) == 1:
                where = conditions[0]
            reviews = vectordb.search_reviews(session_id, focus, n_results=max_reviews, where=where)
        else:
            all_reviews = vectordb.get_all_reviews(session_id)
            reviews = []
            for r in all_reviews:
                rating = r.get("metadata", {}).get("rating")
                if "min_rating" in args and (rating is None or rating < args["min_rating"]):
                    continue
                if "max_rating" in args and (rating is None or rating > args["max_rating"]):
                    continue
                reviews.append(r)
            reviews = reviews[:max_reviews]

        if not reviews:
            return {"content": [{"type": "text", "text": json.dumps({"error": "No reviews matched filters."})}]}

        bigram_freq: dict[str, int] = {}
        bigram_reviews: dict[str, list[str]] = {}
        bigram_ratings: dict[str, list[float]] = {}

        for r in reviews:
            words = tokenize(r.get("text", ""))
            rid = r.get("id", "")
            rating = r.get("metadata", {}).get("rating")
            seen_bg: set[str] = set()

            for i in range(len(words) - 1):
                bg = f"{words[i]} {words[i+1]}"
                bigram_freq[bg] = bigram_freq.get(bg, 0) + 1
                if bg not in seen_bg:
                    seen_bg.add(bg)
                    bigram_reviews.setdefault(bg, []).append(rid)
                    if rating is not None:
                        bigram_ratings.setdefault(bg, []).append(rating)

        themes: list[dict] = []
        used: set[str] = set()
        sorted_bg = sorted(bigram_freq.items(), key=lambda x: -x[1])

        for bg, count in sorted_bg:
            if bg in used or count < 2:
                continue
            bg_words = set(bg.split())
            cluster = [bg]
            total_count = count
            for other_bg, other_count in sorted_bg:
                if other_bg in used or other_bg == bg or other_count < 2:
                    continue
                other_words = set(other_bg.split())
                if bg_words & other_words:
                    cluster.append(other_bg)
                    total_count += other_count
                    used.add(other_bg)
            used.add(bg)

            review_ids = bigram_reviews.get(bg, [])
            ratings = bigram_ratings.get(bg, [])
            pct = round(len(review_ids) / len(reviews) * 100, 1) if reviews else 0
            avg_r = round(sum(ratings) / len(ratings), 2) if ratings else None

            themes.append({
                "theme": bg,
                "related_terms": cluster[1:5],
                "frequency": total_count,
                "review_count": len(review_ids),
                "percentage": pct,
                "avg_rating": avg_r,
                "sample_review_ids": review_ids[:3],
            })

            if len(themes) >= 15:
                break

        await emit_tool(
            "extract_themes",
            f"Extracted {len(themes)} themes from {len(reviews)} reviews" + (f" (focus: {focus})" if focus else ""),
            {"focus": focus, "max_reviews": max_reviews},
            {"theme_count": len(themes), "reviews_analysed": len(reviews)},
        )

        return {"content": [{"type": "text", "text": json.dumps({
            "themes": themes,
            "reviews_analysed": len(reviews),
            "focus": focus or "general",
        })}]}

    # ── find_anomalies ───────────────────────────────────────────────

    @tool(
        name="find_anomalies",
        description="Scan the full dataset for data quality issues and suspicious patterns: rating-text mismatches, duplicate reviews, volume spikes, outlier lengths. Use proactively in initial briefings or when asked about data quality/fake reviews.",
        input_schema={"type": "object", "properties": {}},
    )
    async def find_anomalies_tool(args: dict[str, Any]) -> dict[str, Any]:
        all_reviews = vectordb.get_all_reviews(session_id)

        if not all_reviews:
            return {"content": [{"type": "text", "text": json.dumps({"error": "No reviews in database."})}]}

        findings: dict[str, Any] = {}

        # 1. Rating-text mismatches
        negative_phrases = [
            "terrible", "worst", "awful", "waste of money", "don't buy", "returning",
            "horrible", "disgusting", "never again", "rip off", "broken", "defective",
            "unacceptable", "scam", "fraudulent", "garbage",
        ]
        positive_phrases = [
            "amazing", "perfect", "love it", "best ever", "highly recommend",
            "excellent", "fantastic", "outstanding", "incredible", "wonderful",
            "superb", "flawless", "10/10",
        ]
        mismatches = []
        for r in all_reviews:
            rating = r.get("metadata", {}).get("rating")
            text_lower = r.get("text", "").lower()
            if rating is not None and rating >= 4:
                for phrase in negative_phrases:
                    if phrase in text_lower:
                        mismatches.append({"id": r["id"], "rating": rating,
                                          "signal": f"High rating ({rating}) but text contains '{phrase}'",
                                          "text_preview": r["text"][:150]})
                        break
            elif rating is not None and rating <= 2:
                for phrase in positive_phrases:
                    if phrase in text_lower:
                        mismatches.append({"id": r["id"], "rating": rating,
                                          "signal": f"Low rating ({rating}) but text contains '{phrase}'",
                                          "text_preview": r["text"][:150]})
                        break
        if mismatches:
            findings["rating_text_mismatches"] = {
                "count": len(mismatches),
                "severity": "high" if len(mismatches) > len(all_reviews) * 0.05 else "medium",
                "items": mismatches[:10],
            }

        # 2. Duplicate/near-duplicate text
        normalized: dict[str, list[str]] = {}
        opening_map: dict[str, list[str]] = {}
        for r in all_reviews:
            text = re.sub(r'[^\w\s]', '', r.get("text", "").lower().strip())
            normalized.setdefault(text, []).append(r["id"])
            opening = text[:50]
            if len(opening) >= 20:
                opening_map.setdefault(opening, []).append(r["id"])

        exact_dupes = [{"text_preview": k[:150], "review_ids": v, "count": len(v)}
                       for k, v in normalized.items() if len(v) > 1]
        near_dupes = [{"opening": k[:80], "review_ids": v, "count": len(v)}
                      for k, v in opening_map.items() if len(v) > 1]
        exact_id_sets = {frozenset(d["review_ids"]) for d in exact_dupes}
        near_dupes = [d for d in near_dupes if frozenset(d["review_ids"]) not in exact_id_sets]

        if exact_dupes or near_dupes:
            findings["duplicates"] = {
                "exact_duplicates": exact_dupes[:5],
                "near_duplicates": near_dupes[:5],
                "severity": "high" if exact_dupes else "medium",
            }

        # 3. Review volume clustering
        date_counts: dict[str, int] = {}
        for r in all_reviews:
            date = r.get("metadata", {}).get("date", "")
            if date:
                day = date[:10]
                date_counts[day] = date_counts.get(day, 0) + 1

        if date_counts:
            avg_daily = sum(date_counts.values()) / len(date_counts)
            spikes = [{"date": d, "count": c, "multiple": round(c / avg_daily, 1)}
                      for d, c in sorted(date_counts.items())
                      if c >= avg_daily * 3 and c >= 3]
            if spikes:
                findings["volume_spikes"] = {
                    "avg_daily_volume": round(avg_daily, 1),
                    "spikes": spikes[:10],
                    "severity": "medium",
                }

        # 4. Length outliers
        lengths = [len(r.get("text", "")) for r in all_reviews]
        avg_len = sum(lengths) / len(lengths) if lengths else 0
        short_reviews = [{"id": r["id"], "length": len(r.get("text", "")),
                          "text": r.get("text", "")}
                         for r in all_reviews if len(r.get("text", "")) < 20]
        long_reviews = [{"id": r["id"], "length": len(r.get("text", "")),
                         "text_preview": r.get("text", "")[:200]}
                        for r in all_reviews if len(r.get("text", "")) > avg_len * 3]
        if short_reviews or long_reviews:
            findings["length_outliers"] = {
                "avg_length": round(avg_len),
                "short_reviews": short_reviews[:5],
                "long_reviews": long_reviews[:5],
                "severity": "low",
            }

        await emit_tool(
            "find_anomalies",
            f"Anomaly scan complete: {len(findings)} categories flagged",
            {},
            {"categories": list(findings.keys())},
        )

        return {"content": [{"type": "text", "text": json.dumps({
            "total_reviews_scanned": len(all_reviews),
            "findings": findings,
            "categories_flagged": len(findings),
            "instruction": "Interpret these findings through an ORM lens. Use read_knowledge_file with 'analysis-patterns' for context on what each pattern means. Not all anomalies are problems — distinguish signal from noise.",
        })}]}

    return [compare_segments_tool, extract_themes_tool, find_anomalies_tool]
