"""MCP tools for the ReviewLens agent, using create_sdk_mcp_server."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Callable, Awaitable

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import vectordb

# Type alias for the SSE emit callback
EmitFn = Callable[[str, str, str], Awaitable[None]]


def create_review_tools_server(
    session_id: str,
    emit_fn: EmitFn,
):
    """Create the MCP server with all review analysis tools.

    Like briefbot, uses closure over session_id so tools access the right data.
    """

    # ── search_reviews ───────────────────────────────────────────────

    @tool(
        name="search_reviews",
        description="Semantic search over the ingested review database. Use this to find reviews relevant to the user's question. Returns the most relevant reviews ranked by similarity.",
        schema={
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
            },
            "required": ["query"],
        },
    )
    async def search_reviews_tool(args: dict[str, Any]) -> dict[str, Any]:
        query = args["query"]
        n = min(args.get("n_results", 10), 25)

        where = None
        if "min_rating" in args and "max_rating" in args:
            where = {
                "$and": [
                    {"rating": {"$gte": args["min_rating"]}},
                    {"rating": {"$lte": args["max_rating"]}},
                ]
            }
        elif "min_rating" in args:
            where = {"rating": {"$gte": args["min_rating"]}}
        elif "max_rating" in args:
            where = {"rating": {"$lte": args["max_rating"]}}

        results = vectordb.search_reviews(session_id, query, n_results=n, where=where)

        await emit_fn(
            session_id,
            f"Searched reviews: \"{query}\" — {len(results)} results",
            "tool",
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "query": query,
                            "result_count": len(results),
                            "results": results,
                            "note": "If no results are relevant, tell the user you couldn't find matching reviews. Do NOT make up information.",
                        }
                    ),
                }
            ]
        }

    # ── analyze_sentiment ────────────────────────────────────────────

    @tool(
        name="analyze_sentiment",
        description="Analyse sentiment and extract aspects from reviews matching a query. Returns aspect-sentiment pairs and overall sentiment distribution. Use for questions about what people like/dislike, pain points, praise, etc.",
        schema={
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
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": "No reviews found matching query.", "results": []}),
                    }
                ]
            }

        # Provide the raw reviews to the LLM — it does the actual sentiment analysis.
        # This is more flexible and accurate than a rule-based approach.
        await emit_fn(
            session_id,
            f"Analysing sentiment: \"{query}\" — {len(results)} reviews",
            "tool",
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
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
                        }
                    ),
                }
            ]
        }

    # ── generate_chart ───────────────────────────────────────────────

    @tool(
        name="generate_chart",
        description="Generate a Chart.js chart configuration that renders inline in the chat. Use for visualising rating distributions, sentiment breakdowns, trends over time, aspect comparisons, etc. The chart renders automatically — just return valid config.",
        schema={
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "line", "pie", "doughnut"],
                    "description": "The type of chart to generate.",
                },
                "title": {
                    "type": "string",
                    "description": "Chart title displayed above the visualisation.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "X-axis labels (categories, dates, etc.).",
                },
                "datasets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "data": {
                                "type": "array",
                                "items": {"type": "number"},
                            },
                        },
                        "required": ["label", "data"],
                    },
                    "description": "One or more data series.",
                },
            },
            "required": ["chart_type", "title", "labels", "datasets"],
        },
    )
    async def generate_chart_tool(args: dict[str, Any]) -> dict[str, Any]:
        chart_config = {
            "type": args["chart_type"],
            "title": args["title"],
            "data": {
                "labels": args["labels"],
                "datasets": args["datasets"],
            },
        }

        await emit_fn(
            session_id,
            f"Generated chart: {args['title']}",
            "tool",
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "chart": chart_config,
                            "instruction": "This chart will render automatically in the chat. Reference it in your text response.",
                        }
                    ),
                }
            ]
        }

    # ── calculate_stats ──────────────────────────────────────────────

    @tool(
        name="calculate_stats",
        description="Calculate aggregate statistics over the full review dataset. Use for quantitative questions: average ratings, distributions, counts by category, trends over time periods, etc.",
        schema={
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
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": "No reviews in database."}),
                    }
                ]
            }

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
                    month = date_str[:7]  # YYYY-MM
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
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({"error": "keyword parameter required"}),
                        }
                    ]
                }
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

        await emit_fn(
            session_id,
            f"Calculated stats: {operation}",
            "tool",
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result),
                }
            ]
        }

    # ── suggest_follow_ups ───────────────────────────────────────────

    @tool(
        name="suggest_follow_ups",
        description="Generate contextual follow-up question buttons based on the current conversation. Call this at the END of every response. The questions should be specific to what was just discussed and lead the user deeper into the data.",
        schema={
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-4 contextual follow-up questions. Make them specific and actionable.",
                    "minItems": 2,
                    "maxItems": 4,
                },
            },
            "required": ["questions"],
        },
    )
    async def suggest_follow_ups_tool(args: dict[str, Any]) -> dict[str, Any]:
        questions = args["questions"]

        await emit_fn(
            session_id,
            f"Suggested {len(questions)} follow-up questions",
            "tool",
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "follow_ups": questions,
                            "instruction": "These will appear as clickable buttons below your message. Do not repeat them in your text response.",
                        }
                    ),
                }
            ]
        }

    # ── Build and return the MCP server ──────────────────────────────

    return create_sdk_mcp_server(
        name="reviewlens",
        version="1.0.0",
        tools=[
            search_reviews_tool,
            analyze_sentiment_tool,
            generate_chart_tool,
            calculate_stats_tool,
            suggest_follow_ups_tool,
        ],
    )
