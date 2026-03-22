"""MCP tools for the ReviewLens agent, using create_sdk_mcp_server."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Callable, Awaitable

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import knowledge, store, vectordb

# Type alias for the SSE emit callback
EmitFn = Callable[[str, str, str], Awaitable[None]]


def create_review_tools_server(
    session_id: str,
    emit_fn: EmitFn,
    tool_records: list[dict] | None = None,
    cited_sources: list[dict] | None = None,
    chart_accumulator: list[dict] | None = None,
    follow_up_accumulator: list[str] | None = None,
):
    """Create the MCP server with all review analysis tools.

    Uses closure over session_id so tools access the right data.
    Accumulator lists are populated by tools and read by agent.py
    to attach to the final ChatMessage.
    """

    # Track which source IDs we've already collected
    _seen_source_ids: set[str] = set()
    if cited_sources is not None:
        _seen_source_ids.update(s.get("id", "") for s in cited_sources)

    async def _emit_tool(
        tool_name: str,
        summary: str,
        inputs: dict[str, Any],
        output_summary: dict[str, Any] | None = None,
    ) -> None:
        """Emit a structured tool event via SSE and record it."""
        record = {
            "tool_name": tool_name,
            "summary": summary,
            "inputs": inputs,
            "output_summary": output_summary or {},
        }
        if tool_records is not None:
            tool_records.append(record)
        payload = json.dumps(record)
        await emit_fn(session_id, payload, "tool")

    def _collect_sources(results: list[dict[str, Any]]) -> None:
        """Deduplicate and collect review sources for citation tracking."""
        if cited_sources is None:
            return
        for r in results:
            rid = r.get("id", "")
            if rid and rid not in _seen_source_ids:
                _seen_source_ids.add(rid)
                cited_sources.append({
                    "id": rid,
                    "text": r.get("text", "")[:500],
                    "rating": r.get("metadata", {}).get("rating"),
                    "date": r.get("metadata", {}).get("date"),
                    "author": r.get("metadata", {}).get("author", ""),
                })

    # ── Shared text analysis helpers ────────────────────────────────

    _STOPWORDS = frozenset((
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

    def _tokenize(text: str) -> list[str]:
        """Tokenize text into lowercase content words, filtering stopwords."""
        import re as _re
        words = _re.findall(r'[a-z]+', text.lower())
        return [w for w in words if w not in _STOPWORDS and len(w) > 1]

    # ── search_reviews ───────────────────────────────────────────────

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

        _collect_sources(results)

        await _emit_tool(
            "search_reviews",
            f"Searched reviews: \"{query}\" — {len(results)} results",
            {"query": query, "n_results": n},
            {"result_count": len(results)},
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
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": "No reviews found matching query.", "results": []}),
                    }
                ]
            }

        _collect_sources(results)

        await _emit_tool(
            "analyze_sentiment",
            f"Analysing sentiment: \"{query}\" — {len(results)} reviews",
            {"query": query, "n_reviews": n},
            {"review_count": len(results)},
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
        input_schema={
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

        if chart_accumulator is not None:
            chart_accumulator.append(chart_config)

        await _emit_tool(
            "generate_chart",
            f"Generated chart: {args['title']}",
            {"chart_type": args["chart_type"], "title": args["title"]},
            {"labels_count": len(args["labels"]), "datasets_count": len(args["datasets"])},
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

        await _emit_tool(
            "calculate_stats",
            f"Calculated stats: {operation}",
            {"operation": operation, "keyword": args.get("keyword", "")},
            {k: v for k, v in result.items() if k != "operation"},
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
        input_schema={
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

        if follow_up_accumulator is not None:
            follow_up_accumulator.extend(questions)

        await _emit_tool(
            "suggest_follow_ups",
            f"Suggested {len(questions)} follow-up questions",
            {"count": len(questions)},
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

    # ── list_knowledge_files ─────────────────────────────────────────

    @tool(
        name="list_knowledge_files",
        description="List available ORM domain reference files with one-line summaries. Call this to discover what analytical frameworks, analysis templates, and report structures are available in the knowledge library.",
        input_schema={
            "type": "object",
            "properties": {},
        },
    )
    async def list_knowledge_files_tool(args: dict[str, Any]) -> dict[str, Any]:
        files = knowledge.list_files()

        await _emit_tool(
            "list_knowledge_files",
            f"Knowledge library: {len(files)} files available",
            {},
            {"file_count": len(files)},
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "files": files,
                            "instruction": "Use read_knowledge_file with a file name to read its contents when you need analytical frameworks or templates.",
                        }
                    ),
                }
            ]
        }

    # ── read_knowledge_file ──────────────────────────────────────────

    @tool(
        name="read_knowledge_file",
        description="Read a specific ORM domain reference file by name. Use this to access analytical frameworks, analysis pattern templates, or report structure guides.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The file name (without .md extension). Use list_knowledge_files to see available names.",
                },
            },
            "required": ["name"],
        },
    )
    async def read_knowledge_file_tool(args: dict[str, Any]) -> dict[str, Any]:
        name = args["name"]
        content = knowledge.get(name)

        if content is None:
            available = [f["name"] for f in knowledge.list_files()]
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "error": f"Knowledge file '{name}' not found.",
                                "available": available,
                            }
                        ),
                    }
                ]
            }

        await _emit_tool(
            "read_knowledge_file",
            f"Read knowledge file: {name} ({len(content)} chars)",
            {"name": name},
            {"chars": len(content)},
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "name": name,
                            "content": content,
                        }
                    ),
                }
            ]
        }

    # ── save_to_report ───────────────────────────────────────────────

    @tool(
        name="save_to_report",
        description="Save a key finding to the running analysis report. Use this to bookmark important insights as you discover them during conversation. The user can later ask you to compile these into a full report.",
        input_schema={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": [
                        "executive_summary",
                        "key_findings",
                        "sentiment_overview",
                        "risk_signals",
                        "recommendations",
                        "dataset_overview",
                    ],
                    "description": "The report section to save this finding under.",
                },
                "content": {
                    "type": "string",
                    "description": "The finding content in markdown. Be specific — include data points, quotes, and percentages.",
                },
            },
            "required": ["section", "content"],
        },
    )
    async def save_to_report_tool(args: dict[str, Any]) -> dict[str, Any]:
        section = args["section"]
        content = args["content"]

        store.append_finding(session_id, section, content)

        await _emit_tool(
            "save_to_report",
            f"Saved finding to report: {section}",
            {"section": section},
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "saved": True,
                            "section": section,
                            "instruction": "Finding saved. Continue your response — do not mention the save action to the user unless they asked about the report.",
                        }
                    ),
                }
            ]
        }

    # ── get_report ───────────────────────────────────────────────────

    @tool(
        name="get_report",
        description="Retrieve all saved report findings for this session. Use this when the user asks to generate a report, see a summary, or review what's been captured. Returns findings organised by section.",
        input_schema={
            "type": "object",
            "properties": {},
        },
    )
    async def get_report_tool(args: dict[str, Any]) -> dict[str, Any]:
        findings = store.get_findings(session_id)

        total = sum(len(v) for v in findings.values())

        await _emit_tool(
            "get_report",
            f"Retrieved report: {total} findings across {len(findings)} sections",
            {},
            {"total_findings": total, "sections": len(findings)},
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "findings": findings,
                            "total_findings": total,
                            "instruction": (
                                "Compile these findings into a structured report. "
                                "Use read_knowledge_file with 'report-structure' for the template. "
                                "If no findings are saved yet, tell the user and suggest exploring the data first."
                            ),
                        }
                    ),
                }
            ]
        }

    # ── check_scope ──────────────────────────────────────────────────

    @tool(
        name="check_scope",
        description="Validate whether a question can be answered from the ingested dataset. Call this when a user's question feels borderline or ambiguous — it checks against the dataset metadata (platform, product, review count) and returns a scope assessment.",
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The user's question to validate against the dataset scope.",
                },
            },
            "required": ["question"],
        },
    )
    async def check_scope_tool(args: dict[str, Any]) -> dict[str, Any]:
        question = args["question"].lower()

        session = store.load_session(session_id)
        if not session:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": "Session not found."}),
                    }
                ]
            }

        summary = session.summary
        review_count = vectordb.get_review_count(session_id)

        # Check for out-of-scope signals
        out_of_scope_signals = []

        # General knowledge / non-review questions
        general_keywords = [
            "weather", "news", "stock", "politics", "sports",
            "recipe", "directions", "translate", "code", "program",
            "write me", "tell me a joke", "who is", "what year",
        ]
        for kw in general_keywords:
            if kw in question:
                out_of_scope_signals.append(f"Question contains general-knowledge indicator: '{kw}'")

        # Platform mismatch
        other_platforms = ["amazon", "google maps", "yelp", "trustpilot", "g2", "capterra", "tripadvisor"]
        current_platform = (summary.platform or "").lower()
        for plat in other_platforms:
            if plat in question and plat not in current_platform:
                out_of_scope_signals.append(f"Question references platform '{plat}' but data is from '{summary.platform}'")

        # Determine scope status
        if out_of_scope_signals:
            status = "out_of_scope"
        elif review_count == 0:
            status = "no_data"
            out_of_scope_signals.append("No reviews in database")
        else:
            status = "in_scope"

        await _emit_tool(
            "check_scope",
            f"Scope check: {status}",
            {"question": args["question"][:100]},
            {"status": status},
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "status": status,
                            "dataset": {
                                "product": summary.product_name,
                                "platform": summary.platform,
                                "review_count": review_count,
                                "date_range": summary.date_range,
                            },
                            "signals": out_of_scope_signals,
                            "instruction": {
                                "in_scope": "Question appears answerable from this dataset. Proceed with search_reviews.",
                                "out_of_scope": "Question is outside the dataset scope. Refuse gracefully and suggest an alternative.",
                                "no_data": "No review data available. Ask the user to upload reviews first.",
                            }.get(status, ""),
                        }
                    ),
                }
            ]
        }

    # ── compare_segments ──────────────────────────────────────────────

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
            """Filter reviews by segment criteria."""
            if seg.get("query"):
                # Use semantic search with filters
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
                # Filter from all reviews
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
            """Extract top n-gram terms from review texts."""
            freq: dict[str, int] = {}
            for r in reviews:
                words = _tokenize(r.get("text", ""))
                # Bigrams
                for i in range(len(words) - 1):
                    bg = f"{words[i]} {words[i+1]}"
                    freq[bg] = freq.get(bg, 0) + 1
                # Unigrams (content words only, 4+ chars)
                for w in words:
                    if len(w) >= 4:
                        freq[w] = freq.get(w, 0) + 1
            return sorted(freq.items(), key=lambda x: -x[1])[:n]

        all_reviews = vectordb.get_all_reviews(session_id)
        seg_a = args["segment_a"]
        seg_b = args["segment_b"]

        reviews_a = _filter_reviews(seg_a, all_reviews)
        reviews_b = _filter_reviews(seg_b, all_reviews)

        _collect_sources(reviews_a[:10])
        _collect_sources(reviews_b[:10])

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

        # Find unique and shared terms
        terms_a = {t for t, _ in _top_terms(reviews_a, 20)}
        terms_b = {t for t, _ in _top_terms(reviews_b, 20)}

        await _emit_tool(
            "compare_segments",
            f"Compared: \"{seg_a['label']}\" ({len(reviews_a)}) vs \"{seg_b['label']}\" ({len(reviews_b)})",
            {"segment_a": seg_a["label"], "segment_b": seg_b["label"]},
            {"count_a": len(reviews_a), "count_b": len(reviews_b)},
        )

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "segment_a": result_a,
                    "segment_b": result_b,
                    "unique_to_a": list(terms_a - terms_b)[:8],
                    "unique_to_b": list(terms_b - terms_a)[:8],
                    "shared_terms": list(terms_a & terms_b)[:8],
                }),
            }]
        }

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
            # Semantic search with optional rating filter
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
            # Apply rating filters
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

        # N-gram frequency extraction
        bigram_freq: dict[str, int] = {}
        trigram_freq: dict[str, int] = {}
        # Track which reviews contain each n-gram
        bigram_reviews: dict[str, list[str]] = {}
        bigram_ratings: dict[str, list[float]] = {}

        for r in reviews:
            words = _tokenize(r.get("text", ""))
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

            for i in range(len(words) - 2):
                tg = f"{words[i]} {words[i+1]} {words[i+2]}"
                trigram_freq[tg] = trigram_freq.get(tg, 0) + 1

        # Cluster related n-grams into themes
        themes: list[dict] = []
        used: set[str] = set()
        # Sort by frequency
        sorted_bg = sorted(bigram_freq.items(), key=lambda x: -x[1])

        for bg, count in sorted_bg:
            if bg in used or count < 2:
                continue
            # Find related bigrams (share a content word)
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

            # Theme stats
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

        await _emit_tool(
            "extract_themes",
            f"Extracted {len(themes)} themes from {len(reviews)} reviews" + (f" (focus: {focus})" if focus else ""),
            {"focus": focus, "max_reviews": max_reviews},
            {"theme_count": len(themes), "reviews_analysed": len(reviews)},
        )

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "themes": themes,
                    "reviews_analysed": len(reviews),
                    "focus": focus or "general",
                }),
            }]
        }

    # ── find_anomalies ───────────────────────────────────────────────

    @tool(
        name="find_anomalies",
        description="Scan the full dataset for data quality issues and suspicious patterns: rating-text mismatches, duplicate reviews, volume spikes, outlier lengths. Use proactively in initial briefings or when asked about data quality/fake reviews.",
        input_schema={
            "type": "object",
            "properties": {},
        },
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
        import re as _re
        normalized: dict[str, list[str]] = {}
        opening_map: dict[str, list[str]] = {}
        for r in all_reviews:
            text = _re.sub(r'[^\w\s]', '', r.get("text", "").lower().strip())
            normalized.setdefault(text, []).append(r["id"])
            opening = text[:50]
            if len(opening) >= 20:
                opening_map.setdefault(opening, []).append(r["id"])

        exact_dupes = [{"text_preview": k[:150], "review_ids": v, "count": len(v)}
                       for k, v in normalized.items() if len(v) > 1]
        near_dupes = [{"opening": k[:80], "review_ids": v, "count": len(v)}
                      for k, v in opening_map.items() if len(v) > 1]
        # Remove near-dupes that are also exact dupes
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

        await _emit_tool(
            "find_anomalies",
            f"Anomaly scan complete: {len(findings)} categories flagged",
            {},
            {"categories": list(findings.keys())},
        )

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "total_reviews_scanned": len(all_reviews),
                    "findings": findings,
                    "categories_flagged": len(findings),
                    "instruction": "Interpret these findings through an ORM lens. Use read_knowledge_file with 'analysis-patterns' for context on what each pattern means. Not all anomalies are problems — distinguish signal from noise.",
                }),
            }]
        }

    # ── get_review_by_id ─────────────────────────────────────────────

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

        _collect_sources([review])

        # Check for other reviews by same author
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

        await _emit_tool(
            "get_review_by_id",
            f"Retrieved review: {review_id}",
            {"review_id": review_id},
        )

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "review": {
                        "id": review["id"],
                        "text": review["text"],
                        "metadata": review["metadata"],
                    },
                    "same_author_reviews": same_author[:5] if same_author else [],
                }),
            }]
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
            list_knowledge_files_tool,
            read_knowledge_file_tool,
            save_to_report_tool,
            get_report_tool,
            check_scope_tool,
            compare_segments_tool,
            extract_themes_tool,
            find_anomalies_tool,
            get_review_by_id_tool,
        ],
    )
