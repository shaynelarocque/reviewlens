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
        ],
    )
