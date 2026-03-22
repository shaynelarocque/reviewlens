"""MCP tools package for the ReviewLens agent."""

from __future__ import annotations

from claude_agent_sdk import create_sdk_mcp_server

from ._helpers import EmitFn, make_emit_tool, make_collect_sources
from .data_tools import create_data_tools
from .analysis_tools import create_analysis_tools
from .presentation_tools import create_presentation_tools
from .knowledge_tools import create_knowledge_tools
from .report_tools import create_report_tools


def create_review_tools_server(
    session_id: str,
    emit_fn: EmitFn,
    tool_records: list[dict] | None = None,
    cited_sources: list[dict] | None = None,
    chart_accumulator: list[dict] | None = None,
    follow_up_accumulator: list[str] | None = None,
):
    """Create the MCP server with all review analysis tools.

    Accumulator lists are populated by tools and read by agent.py
    to attach to the final ChatMessage.
    """
    # Set up shared state
    seen_source_ids: set[str] = set()
    if cited_sources is not None:
        seen_source_ids.update(s.get("id", "") for s in cited_sources)

    emit_tool = make_emit_tool(session_id, emit_fn, tool_records)
    collect_sources = make_collect_sources(cited_sources, seen_source_ids)

    # Collect all tools from each module
    tools = []
    tools += create_data_tools(session_id, emit_tool, collect_sources)
    tools += create_analysis_tools(session_id, emit_tool, collect_sources)
    tools += create_presentation_tools(emit_tool, chart_accumulator, follow_up_accumulator)
    tools += create_knowledge_tools(emit_tool)
    tools += create_report_tools(session_id, emit_tool)

    return create_sdk_mcp_server(name="reviewlens", version="1.0.0", tools=tools)
