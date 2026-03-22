"""Knowledge base tools — list and read reference files."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from .. import knowledge
from ._helpers import EmitToolFn


def create_knowledge_tools(emit_tool: EmitToolFn) -> list:
    """Return knowledge tool definitions."""

    @tool(
        name="list_knowledge_files",
        description="List available ORM domain reference files with one-line summaries. Call this to discover what analytical frameworks, analysis templates, and report structures are available in the knowledge library.",
        input_schema={"type": "object", "properties": {}},
    )
    async def list_knowledge_files_tool(args: dict[str, Any]) -> dict[str, Any]:
        files = knowledge.list_files()

        await emit_tool(
            "list_knowledge_files",
            f"Knowledge library: {len(files)} files available",
            {},
            {"file_count": len(files)},
        )

        return {"content": [{"type": "text", "text": json.dumps({
            "files": files,
            "instruction": "Use read_knowledge_file with a file name to read its contents when you need analytical frameworks or templates.",
        })}]}

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
            return {"content": [{"type": "text", "text": json.dumps({
                "error": f"Knowledge file '{name}' not found.",
                "available": available,
            })}]}

        await emit_tool(
            "read_knowledge_file",
            f"Read knowledge file: {name} ({len(content)} chars)",
            {"name": name},
            {"chars": len(content)},
        )

        return {"content": [{"type": "text", "text": json.dumps({"name": name, "content": content})}]}

    return [list_knowledge_files_tool, read_knowledge_file_tool]
