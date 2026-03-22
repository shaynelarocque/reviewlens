"""Data access tools — search, sentiment, stats, review lookup."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from claude_agent_sdk import tool

from .. import vectordb
from ._helpers import EmitToolFn, CollectSourcesFn


def create_data_tools(
    session_id: str,
    emit_tool: EmitToolFn,
    collect_sources: CollectSourcesFn,
) -> list:
    """Return data access tool definitions."""

    @tool(
        name="search_reviews",
        description="Semantic search over the ingested review database. Use this to find reviews relevant to the user's question. Returns the most relevant reviews ranked by similarity.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query — describe what you're looking for in natural language.",
                },
                "n_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 10, max 25).",
                    "default": 10,
                },
                "min_rating": {
                    "type": "number",
                    "description": "Optional: only return reviews with rating >= this value.",
                },
                "max_rating": {
                    "type": "number",
                    "description": "Optional: only return reviews with rating <= this value.",
                },
                "date_after": {
                    "type": "string",
                    "description": "Optional: only return reviews after this date (ISO format, e.g. '2024-06-01').",
                },
                "date_before": {
                    "type": "string",
                    "description": "Optional: only return reviews before this date (ISO format, e.g. '2024-09-30').",
                },
            },
            "required": ["query"],
        },
    )
    async def search_reviews_tool(args: dict[str, Any]) -> dict[str, Any]:
        query = args["query"]
        n = min(args.get("n_results", 10), 25)

        conditions = []
        if "min_rating" in args:
            conditions.append({"rating": {"$gte": args["min_rating"]}})
        if "max_rating" in args:
            conditions.append({"rating": {"$lte": args["max_rating"]}})
        if "date_after" in args:
            conditions.append({"date": {"$gte": args["date_after"]}})
        if "date_before" in args:
            conditions.append({"date": {"$lte": args["date_before"]}})

        where = None
        if len(conditions) > 1:
            where = {"$and": conditions}
        elif len(conditions) == 1:
            where = conditions[0]

        results = vectordb.search_reviews(session_id, query, n_results=n, where=where)
        collect_sources(results)

        await emit_tool(
            "search_reviews",
            f"Searched reviews: \"{query}\" — {len(results)} results",
            {"query": query, "n_results": n},
            {"result_count": len(results)},
        )

        return {"content": [{"type": "text", "text": json.dumps({
            "query": query,
            "result_count": len(results),
            "results": results,
            "note": "If no results are relevant, tell the user you couldn't find matching reviews. Do NOT make up information.",
        })}]}

    @tool(
        name="analyze_sentiment",
        description="Analyse sentiment and extract aspects from reviews matching a query. Returns aspect-sentiment pairs and overall sentiment distribution. Use for questions about what people like/dislike, pain points, praise, etc.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to find relevant reviews for analysis.",
                },
                "n_reviews": {
                    "type": "integer",
                    "description": "Number of reviews to analyse (default 15, max 30).",
                    "default": 15,
                },
            },
            "required": ["query"],
        },
    )
    async def analyze_sentiment_tool(args: dict[str, Any]) -> dict[str, Any]:
        query = args["query"]
        n = min(args.get("n_reviews", 15), 30)
        results = vectordb.search_reviews(session_id, query, n_results=n)

        if not results:
            return {"content": [{"type": "text", "text": json.dumps({"error": "No reviews found matching query.", "results": []})}]}

        collect_sources(results)

        await emit_tool(
            "analyze_sentiment",
            f"Analysing sentiment: \"{query}\" — {len(results)} reviews",
            {"query": query, "n_reviews": n},
            {"review_count": len(results)},
        )

        return {"content": [{"type": "text", "text": json.dumps({
            "query": query,
            "review_count": len(results),
            "reviews": results,
            "instruction": (
                "Analyse these reviews for sentiment and aspects. "
                "For each review, identify: (1) key aspects mentioned "
                "(e.g., 'battery life', 'customer service', 'price'), "
                "(2) sentiment per aspect (positive/negative/neutral/mixed), "
                "(3) overall sentiment. Then summarise the patterns across all reviews. "
                "Only report what the reviews actually say."
            ),
        })}]}

    @tool(
        name="calculate_stats",
        description="Calculate aggregate statistics over the full review dataset. Use for quantitative questions: average ratings, distributions, counts by category, trends over time periods, etc.",
        input_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "rating_distribution",
                        "rating_over_time",
                        "review_volume_over_time",
                        "keyword_frequency",
                        "summary_stats",
                    ],
                    "description": "The type of statistical analysis to run.",
                },
                "keyword": {
                    "type": "string",
                    "description": "For keyword_frequency: the keyword or phrase to count.",
                },
            },
            "required": ["operation"],
        },
    )
    async def calculate_stats_tool(args: dict[str, Any]) -> dict[str, Any]:
        operation = args["operation"]
        all_reviews = vectordb.get_all_reviews(session_id)

        if not all_reviews:
            return {"content": [{"type": "text", "text": json.dumps({"error": "No reviews in database."})}]}

        result: dict[str, Any] = {"operation": operation}

        if operation == "rating_distribution":
            ratings = [r["metadata"].get("rating") for r in all_reviews if r["metadata"].get("rating") is not None]
            dist = Counter(int(round(r)) for r in ratings)
            result["distribution"] = {str(k): v for k, v in sorted(dist.items())}
            result["total_with_ratings"] = len(ratings)
            result["total_reviews"] = len(all_reviews)
            if ratings:
                result["average"] = round(sum(ratings) / len(ratings), 2)

        elif operation == "rating_over_time":
            by_month: dict[str, list[float]] = {}
            for r in all_reviews:
                date_str = r["metadata"].get("date", "")
                rating = r["metadata"].get("rating")
                if date_str and rating is not None:
                    month = date_str[:7]
                    by_month.setdefault(month, []).append(rating)
            result["monthly_averages"] = {
                m: round(sum(v) / len(v), 2)
                for m, v in sorted(by_month.items())
            }

        elif operation == "review_volume_over_time":
            by_month: dict[str, int] = {}
            for r in all_reviews:
                date_str = r["metadata"].get("date", "")
                if date_str:
                    month = date_str[:7]
                    by_month[month] = by_month.get(month, 0) + 1
            result["monthly_volume"] = dict(sorted(by_month.items()))

        elif operation == "keyword_frequency":
            keyword = args.get("keyword", "").lower()
            if not keyword:
                return {"content": [{"type": "text", "text": json.dumps({"error": "keyword parameter required"})}]}
            count = sum(1 for r in all_reviews if keyword in r["text"].lower())
            result["keyword"] = keyword
            result["count"] = count
            result["percentage"] = round(count / len(all_reviews) * 100, 1)

        elif operation == "summary_stats":
            ratings = [r["metadata"].get("rating") for r in all_reviews if r["metadata"].get("rating") is not None]
            result["total_reviews"] = len(all_reviews)
            result["total_with_ratings"] = len(ratings)
            if ratings:
                result["average_rating"] = round(sum(ratings) / len(ratings), 2)
                result["min_rating"] = min(ratings)
                result["max_rating"] = max(ratings)
            dates = [r["metadata"].get("date", "") for r in all_reviews if r["metadata"].get("date")]
            if dates:
                result["earliest_review"] = min(dates)
                result["latest_review"] = max(dates)
            avg_length = sum(len(r["text"]) for r in all_reviews) / len(all_reviews)
            result["average_review_length"] = round(avg_length)

        await emit_tool(
            "calculate_stats",
            f"Calculated stats: {operation}",
            {"operation": operation, "keyword": args.get("keyword", "")},
            {k: v for k, v in result.items() if k != "operation"},
        )

        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        name="get_review_by_id",
        description="Look up a specific review by its ID. Use when the user references a specific review from a prior answer, or when cross-referencing a cited review.",
        input_schema={
            "type": "object",
            "properties": {
                "review_id": {
                    "type": "string",
                    "description": "The review ID to look up (e.g., 'review_42').",
                },
            },
            "required": ["review_id"],
        },
    )
    async def get_review_by_id_tool(args: dict[str, Any]) -> dict[str, Any]:
        review_id = args["review_id"]
        review = vectordb.get_review_by_id(session_id, review_id)

        if not review:
            return {"content": [{"type": "text", "text": json.dumps({"error": f"Review '{review_id}' not found."})}]}

        collect_sources([review])

        author = review.get("metadata", {}).get("author", "")
        same_author = []
        if author:
            all_reviews = vectordb.get_all_reviews(session_id)
            same_author = [
                {"id": r["id"], "rating": r.get("metadata", {}).get("rating"),
                 "text_preview": r.get("text", "")[:150]}
                for r in all_reviews
                if r.get("metadata", {}).get("author") == author and r["id"] != review_id
            ]

        await emit_tool("get_review_by_id", f"Retrieved review: {review_id}", {"review_id": review_id})

        return {"content": [{"type": "text", "text": json.dumps({
            "review": {"id": review["id"], "text": review["text"], "metadata": review["metadata"]},
            "same_author_reviews": same_author[:5] if same_author else [],
        })}]}

    return [search_reviews_tool, analyze_sentiment_tool, calculate_stats_tool, get_review_by_id_tool]
