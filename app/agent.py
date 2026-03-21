"""Conversational agent loop — invoked per-message, not per-session."""

from __future__ import annotations

import json
import os
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
)

from .models import ChatMessage, IngestionSummary
from .prompts import build_system_prompt
from .tools import create_review_tools_server

DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6-20250514")


async def handle_message(
    session_id: str,
    user_message: str,
    conversation_history: list[ChatMessage],
    summary: IngestionSummary,
    emit_fn,
) -> ChatMessage:
    """Run the agent for a single user message. Returns the assistant's response."""

    system_prompt = build_system_prompt(summary)

    # Build conversation context for the agent
    messages_for_context = []
    for msg in conversation_history[-20:]:  # Last 20 messages for context window
        messages_for_context.append(f"{'User' if msg.role == 'user' else 'Assistant'}: {msg.content}")

    prompt_parts = []
    if messages_for_context:
        prompt_parts.append("Previous conversation:\n" + "\n".join(messages_for_context))
    prompt_parts.append(f"User: {user_message}")
    prompt = "\n\n".join(prompt_parts)

    # Create per-request MCP server (closure over session_id)
    server = create_review_tools_server(
        session_id=session_id,
        emit_fn=emit_fn,
    )

    # Track tool outputs for charts and follow-ups
    charts: list[dict[str, Any]] = []
    follow_ups: list[str] = []

    async def post_tool_hook(input_data, tool_use_id, context):
        """Intercept tool results to extract charts and follow-ups."""
        # The tool result is in context
        try:
            result = context.get("result", {})
            content = result.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    data = json.loads(block["text"])
                    if "chart" in data:
                        charts.append(data["chart"])
                    if "follow_ups" in data:
                        follow_ups.extend(data["follow_ups"])
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return {}

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["mcp__reviewlens__*"],
        permission_mode="bypassPermissions",
        max_turns=15,
        model=DEFAULT_MODEL,
        mcp_servers={"reviewlens": server},
        hooks={
            "PostToolUse": [HookMatcher(matcher=".*", hooks=[post_tool_hook])],
        },
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
    )
