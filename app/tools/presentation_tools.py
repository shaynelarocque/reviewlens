"""Presentation tools — charts and follow-up suggestions."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from ._helpers import EmitToolFn


def create_presentation_tools(
    emit_tool: EmitToolFn,
    chart_accumulator: list[dict] | None = None,
    follow_up_accumulator: list[str] | None = None,
) -> list:
    """Return presentation tool definitions."""

    @tool(
        name="generate_chart",
        description="Generate a Chart.js chart configuration that renders inline in the chat. Use for visualising rating distributions, sentiment breakdowns, trends over time, aspect comparisons, etc. The chart renders automatically — just return valid config.",
        input_schema={
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "horizontalBar", "stacked_bar", "line", "pie", "doughnut", "radar", "scatter"],
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
                            "data": {"type": "array", "items": {"type": "number"}},
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
            "data": {"labels": args["labels"], "datasets": args["datasets"]},
        }

        if chart_accumulator is not None:
            chart_accumulator.append(chart_config)

        await emit_tool(
            "generate_chart",
            f"Generated chart: {args['title']}",
            {"chart_type": args["chart_type"], "title": args["title"]},
            {"labels_count": len(args["labels"]), "datasets_count": len(args["datasets"])},
        )

        return {"content": [{"type": "text", "text": json.dumps({
            "chart": chart_config,
            "instruction": "This chart will render automatically in the chat. Reference it in your text response.",
        })}]}

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

        await emit_tool(
            "suggest_follow_ups",
            f"Suggested {len(questions)} follow-up questions",
            {"count": len(questions)},
        )

        return {"content": [{"type": "text", "text": json.dumps({
            "follow_ups": questions,
            "instruction": "These will appear as clickable buttons below your message. Do not repeat them in your text response.",
        })}]}

    return [generate_chart_tool, suggest_follow_ups_tool]
