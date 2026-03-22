"""Report and scope tools — save findings, get report, compile PDF, check scope."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from .. import store, vectordb
from ..pdf import generate_pdf
from ._helpers import EmitToolFn


def create_report_tools(session_id: str, emit_tool: EmitToolFn) -> list:
    """Return report and scope tool definitions."""

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

        await emit_tool("save_to_report", f"Saved finding to report: {section}", {"section": section})

        return {"content": [{"type": "text", "text": json.dumps({
            "saved": True,
            "section": section,
            "instruction": "Finding saved. Continue your response — do not mention the save action to the user unless they asked about the report.",
        })}]}

    @tool(
        name="get_report",
        description="Retrieve all saved report findings for this session. Use this when the user asks to generate a report, see a summary, or review what's been captured. Returns findings organised by section.",
        input_schema={"type": "object", "properties": {}},
    )
    async def get_report_tool(args: dict[str, Any]) -> dict[str, Any]:
        findings = store.get_findings(session_id)
        total = sum(len(v) for v in findings.values())

        await emit_tool(
            "get_report",
            f"Retrieved report: {total} findings across {len(findings)} sections",
            {},
            {"total_findings": total, "sections": len(findings)},
        )

        return {"content": [{"type": "text", "text": json.dumps({
            "findings": findings,
            "total_findings": total,
            "instruction": (
                "Compile these findings into a structured report. "
                "Use read_knowledge_file with 'report-structure' for the template. "
                "If no findings are saved yet, tell the user and suggest exploring the data first."
            ),
        })}]}

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
            return {"content": [{"type": "text", "text": json.dumps({"error": "Session not found."})}]}

        summary = session.summary
        review_count = vectordb.get_review_count(session_id)

        out_of_scope_signals = []

        general_keywords = [
            "weather", "news", "stock", "politics", "sports",
            "recipe", "directions", "translate", "code", "program",
            "write me", "tell me a joke", "who is", "what year",
        ]
        for kw in general_keywords:
            if kw in question:
                out_of_scope_signals.append(f"Question contains general-knowledge indicator: '{kw}'")

        other_platforms = ["amazon", "google maps", "yelp", "trustpilot", "g2", "capterra", "tripadvisor"]
        current_platform = (summary.platform or "").lower()
        for plat in other_platforms:
            if plat in question and plat not in current_platform:
                out_of_scope_signals.append(f"Question references platform '{plat}' but data is from '{summary.platform}'")

        if out_of_scope_signals:
            status = "out_of_scope"
        elif review_count == 0:
            status = "no_data"
            out_of_scope_signals.append("No reviews in database")
        else:
            status = "in_scope"

        await emit_tool("check_scope", f"Scope check: {status}", {"question": args["question"][:100]}, {"status": status})

        return {"content": [{"type": "text", "text": json.dumps({
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
        })}]}

    @tool(
        name="compile_report",
        description="Generate a downloadable PDF report from your assembled analysis. Call this when the user explicitly asks for a report/PDF. Pass the full report as markdown content with chart configs. Do NOT call this during the initial auto-analysis — only on explicit user request.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Report title, e.g. 'Sony WH-1000XM5 Review Intelligence Report'.",
                },
                "content": {
                    "type": "string",
                    "description": "The full report as markdown. Assemble from get_report findings and the report-structure knowledge file.",
                },
                "charts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "title": {"type": "string"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "labels": {"type": "array", "items": {"type": "string"}},
                                    "datasets": {"type": "array"},
                                },
                            },
                        },
                    },
                    "description": "Optional chart configs to render in the PDF (same format as generate_chart). Use [chart:N] markers in the content to position them.",
                },
            },
            "required": ["title", "content"],
        },
    )
    async def compile_report_tool(args: dict[str, Any]) -> dict[str, Any]:
        title = args["title"]
        content = args["content"]
        charts = args.get("charts", [])

        session = store.load_session(session_id)
        if not session:
            return {"content": [{"type": "text", "text": json.dumps({"error": "Session not found."})}]}

        try:
            pdf_bytes = generate_pdf(
                title=title,
                content_md=content,
                summary=session.summary,
                charts=charts,
            )

            # Save to data directory
            report_path = Path(store._session_dir(session_id)) / "report.pdf"
            report_path.write_bytes(pdf_bytes)

            # Update session metadata
            session.report_generated_at = datetime.utcnow()
            store.save_session(session)

            download_url = f"/api/report/{session_id}/download"

            await emit_tool(
                "compile_report",
                f"Report compiled: {title} ({len(pdf_bytes) // 1024}KB PDF)",
                {"title": title},
                {"size_kb": len(pdf_bytes) // 1024},
            )

            return {"content": [{"type": "text", "text": json.dumps({
                "success": True,
                "title": title,
                "download_url": download_url,
                "size_kb": len(pdf_bytes) // 1024,
                "instruction": "Present the download link to the user. The system will render it as a download card.",
            })}]}

        except Exception as e:
            await emit_tool("compile_report", f"Report compilation failed: {e}", {"title": title})
            return {"content": [{"type": "text", "text": json.dumps({
                "error": f"Failed to compile PDF: {str(e)}",
            })}]}

    return [save_to_report_tool, get_report_tool, compile_report_tool, check_scope_tool]
