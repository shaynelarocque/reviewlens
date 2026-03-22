"""Conversational agent loop — invoked per-message, not per-session."""

from __future__ import annotations

import json
import os
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from .models import ChatMessage, IngestionSummary, ToolCallRecord
from .prompts import build_system_prompt
from .tools import create_review_tools_server

DEFAULT_MODEL = "claude-sonnet-4-6"

# How many recent messages to pass in full before summarising
RECENT_WINDOW = 10
# Max older messages to summarise
SUMMARY_WINDOW = 30


def _build_conversation_context(
    conversation_history: list[ChatMessage],
    user_message: str,
) -> str:
    """Build structured conversation context for the agent.

    Strategy:
    - Recent messages (last RECENT_WINDOW) are passed in full with role labels
    - Older messages are compressed into a topic summary
    - A session context block tracks what's been explored
    """
    total = len(conversation_history)
    parts: list[str] = []

    if total > 0:
        # Split into older and recent
        recent_start = max(0, total - RECENT_WINDOW)
        older = conversation_history[max(0, recent_start - SUMMARY_WINDOW):recent_start]
        recent = conversation_history[recent_start:]

        # Summarise older messages as topic bullets
        if older:
            topics = _extract_topics(older)
            parts.append(
                "## Session Context\n"
                f"This is message {total + 1} in the conversation. "
                f"Earlier topics explored:\n{topics}"
            )

        # Pass recent messages with structure
        if recent:
            lines = []
            for msg in recent:
                role = "User" if msg.role == "user" else "Assistant"
                # Truncate very long assistant responses in context
                content = msg.content
                if msg.role == "assistant" and len(content) > 800:
                    content = content[:800] + "\n[... truncated for context ...]"
                lines.append(f"**{role}:** {content}")
            parts.append("## Recent Conversation\n" + "\n\n".join(lines))

    parts.append(f"## Current Question\n{user_message}")
    return "\n\n".join(parts)


def _extract_topics(messages: list[ChatMessage]) -> str:
    """Extract topic bullets from older messages for context summary."""
    topics: list[str] = []
    for msg in messages:
        if msg.role == "user":
            # Use first 120 chars of user messages as topic indicators
            text = msg.content.strip()
            if len(text) > 120:
                text = text[:120] + "..."
            topics.append(f"- {text}")
    if not topics:
        return "- (general exploration)"
    return "\n".join(topics)


async def handle_message(
    session_id: str,
    user_message: str,
    conversation_history: list[ChatMessage],
    summary: IngestionSummary,
    emit_fn,
) -> ChatMessage:
    """Run the agent for a single user message. Returns the assistant's response."""

    model = os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)
    system_prompt = build_system_prompt(summary)

    # Build structured conversation context
    prompt = _build_conversation_context(conversation_history, user_message)

    # Accumulators populated by tools via closure
    tool_records: list[dict] = []
    cited_sources: list[dict] = []
    charts: list[dict[str, Any]] = []
    follow_ups: list[str] = []

    # Create per-request MCP server (closure over session_id)
    server = create_review_tools_server(
        session_id=session_id,
        emit_fn=emit_fn,
        tool_records=tool_records,
        cited_sources=cited_sources,
        chart_accumulator=charts,
        follow_up_accumulator=follow_ups,
    )

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["mcp__reviewlens__*"],
        permission_mode="bypassPermissions",
        max_turns=15,
        model=model,
        mcp_servers={"reviewlens": server},
    )

    response_text = ""

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            response_text += block.text.strip() + "\n"
                elif isinstance(message, ResultMessage):
                    if message.result and not response_text.strip():
                        response_text = message.result

    except Exception as e:
        response_text = f"I encountered an error processing your question. Please try again.\n\nError: {str(e)}"
        await emit_fn(session_id, f"Agent error: {e}", "error")

    return ChatMessage(
        role="assistant",
        content=response_text.strip(),
        charts=charts,
        follow_ups=follow_ups,
        tool_calls=[ToolCallRecord(**r) for r in tool_records],
        sources=cited_sources,
    )
