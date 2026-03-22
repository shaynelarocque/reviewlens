"""FastAPI application for ReviewLens AI."""

from __future__ import annotations

import asyncio
import html as html_module
import json
import os
import re
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import markdown
from dotenv import load_dotenv
from markupsafe import Markup
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from . import knowledge, store, vectordb
from .agent import handle_message
from .ingest import build_summary, parse_csv, scrape_url
from .models import ChatMessage, IngestionSummary, Session

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
app = FastAPI(title="ReviewLens AI")

# Load knowledge base on startup
knowledge.load()
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _render_message_filter(msg):
    """Jinja2 filter — renders a ChatMessage as full HTML."""
    return Markup(_render_message(msg))


templates.env.filters["render_message"] = _render_message_filter

# ── In-memory SSE event queues (per-session) ────────────────────────
_event_queues: dict[str, deque[dict[str, str]]] = {}
_response_events: dict[str, asyncio.Event] = {}


def _get_queue(session_id: str) -> deque[dict[str, str]]:
    if session_id not in _event_queues:
        _event_queues[session_id] = deque()
    return _event_queues[session_id]


def _get_response_event(session_id: str) -> asyncio.Event:
    if session_id not in _response_events:
        _response_events[session_id] = asyncio.Event()
    return _response_events[session_id]


async def _emit(session_id: str, message: str, level: str = "info") -> None:
    _get_queue(session_id).append({"event": level, "data": message})


# ── Auto-analysis prompt ─────────────────────────────────────────────

_INITIAL_ANALYSIS_PROMPT = (
    "Analyse this dataset and provide an initial intelligence briefing. "
    "This is the analyst's first look at the data — make it count.\n\n"
    "Cover these areas:\n"
    "1. Dataset overview with a rating distribution chart\n"
    "2. Top 3 most praised aspects with specific review citations\n"
    "3. Top 3 complaints or pain points with specific review citations\n"
    "4. Any notable risk signals, emerging trends, or inconsistencies worth flagging\n"
    "5. A brief overall sentiment assessment\n\n"
    "Use multiple search queries with different angles to be thorough. "
    "Generate at least one chart. Save the most significant findings to the report. "
    "End with follow-up suggestions that drill into the most interesting patterns you found.\n\n"
    "Consult the knowledge base (list_knowledge_files → read_knowledge_file) "
    "if you need analytical frameworks for your analysis."
)


def _trigger_auto_analysis(session_id: str, session: Session) -> None:
    """Save a synthetic message and kick off the initial analysis agent run."""
    trigger_msg = ChatMessage(
        role="user",
        content="[initial_analysis]",
        system_initiated=True,
    )
    store.append_message(session_id, trigger_msg)
    _get_response_event(session_id).clear()
    asyncio.create_task(
        _run_agent_and_respond(session_id, _INITIAL_ANALYSIS_PROMPT, session)
    )


# ── Health check ─────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Sample data discovery ────────────────────────────────────────────

SAMPLE_DIR = BASE_DIR / "sample-data"


def _list_sample_files() -> list[dict[str, str]]:
    """List available sample CSVs with human-readable labels."""
    if not SAMPLE_DIR.exists():
        return []
    files = []
    for f in sorted(SAMPLE_DIR.glob("*.csv")):
        label = f.stem.replace("_", " ").replace("-", " ").title()
        files.append({"filename": f.name, "label": label})
    return files


# ── App shell (home + chat share the same layout) ───────────────────

def _shell_context(request: Request, session=None):
    """Build template context for the app shell."""
    sessions = store.list_sessions()

    # Filter out system-initiated messages for display
    messages = session.messages if session else []
    visible_messages = [m for m in messages if not (m.system_initiated and m.role == "user")]

    # Detect if auto-analysis is in progress (has trigger message but no assistant response yet)
    auto_analysis = False
    if session and session.status == "ready":
        has_trigger = any(m.system_initiated for m in messages)
        has_response = any(m.role == "assistant" for m in messages)
        auto_analysis = has_trigger and not has_response

    ctx = {
        "request": request,
        "sessions": sessions,
        "session": session,
        "summary": session.summary if session else None,
        "messages": visible_messages,
        "active_id": session.session_id if session else None,
        "sample_files": _list_sample_files(),
        "auto_analysis": auto_analysis,
    }
    return ctx


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("app.html", _shell_context(request))


@app.get("/chat/{session_id}", response_class=HTMLResponse)
async def chat_page(request: Request, session_id: str):
    session = store.load_session(session_id)
    if not session:
        return HTMLResponse("<h1>Session not found</h1>", status_code=404)
    return templates.TemplateResponse("app.html", _shell_context(request, session))


# ── CSV Upload ───────────────────────────────────────────────────────

@app.post("/upload")
async def upload_csv(
    request: Request,
    file: UploadFile = File(...),
    product_name: str = Form(""),
    platform: str = Form(""),
):
    session_id = str(uuid.uuid4())

    content = await file.read()
    try:
        reviews = await parse_csv(content, platform=platform, product_name=product_name)
    except Exception as e:
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": f"Failed to parse CSV: {e}"},
            status_code=400,
        )

    if not reviews:
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": "No reviews found in the CSV. Make sure it has a text/review column."},
            status_code=400,
        )

    summary = build_summary(reviews, source_type="csv", product_name=product_name, platform=platform)

    session = Session(
        session_id=session_id,
        product_name=summary.product_name,
        platform=summary.platform,
        summary=summary,
        status="indexing",
    )
    store.save_session(session)
    store.save_reviews_json(session_id, [r.model_dump(mode="json") for r in reviews])

    indexed = vectordb.index_reviews(session_id, reviews)
    summary.total_reviews = indexed
    store.update_summary(session_id, summary)
    store.set_status(session_id, "ready")

    # Kick off auto-analysis
    session = store.load_session(session_id)
    if session:
        _trigger_auto_analysis(session_id, session)

    return HTMLResponse(
        status_code=200,
        content=f'<script>window.location.href="/chat/{session_id}";</script>',
        headers={"HX-Redirect": f"/chat/{session_id}"},
    )


# ── Sample Data ──────────────────────────────────────────────────────

@app.post("/sample")
async def load_sample(
    request: Request,
    filename: str = Form(...),
):
    # Sanitise: only allow filenames that exist in sample-data/
    path = SAMPLE_DIR / filename
    if not path.exists() or not path.suffix == ".csv" or ".." in filename:
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": "Sample file not found."},
            status_code=400,
        )

    content = path.read_bytes()
    # Derive product name and platform from the filename
    stem = path.stem.replace("_", " ").replace("-", " ")
    product_name = stem.title()
    platform = ""
    for plat in ("Amazon", "Google Maps", "G2", "Capterra", "Trustpilot", "Yelp"):
        if plat.lower().replace(" ", "_") in path.stem.lower() or plat.lower().replace(" ", "") in path.stem.lower():
            platform = plat
            break

    reviews = await parse_csv(content, platform=platform, product_name=product_name)

    if not reviews:
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": "Could not parse reviews from this sample file."},
            status_code=400,
        )

    session_id = str(uuid.uuid4())
    summary = build_summary(reviews, source_type="csv", product_name=product_name, platform=platform)

    session = Session(
        session_id=session_id,
        product_name=summary.product_name,
        platform=summary.platform,
        summary=summary,
        status="indexing",
    )
    store.save_session(session)
    store.save_reviews_json(session_id, [r.model_dump(mode="json") for r in reviews])

    indexed = vectordb.index_reviews(session_id, reviews)
    summary.total_reviews = indexed
    store.update_summary(session_id, summary)
    store.set_status(session_id, "ready")

    # Kick off auto-analysis
    session = store.load_session(session_id)
    if session:
        _trigger_auto_analysis(session_id, session)

    return HTMLResponse(
        status_code=200,
        content=f'<script>window.location.href="/chat/{session_id}";</script>',
        headers={"HX-Redirect": f"/chat/{session_id}"},
    )


# ── URL Scrape ───────────────────────────────────────────────────────

@app.post("/scrape")
async def scrape(
    request: Request,
    url: str = Form(...),
    product_name: str = Form(""),
    platform: str = Form(""),
):
    session_id = str(uuid.uuid4())

    # Create session immediately with "scraping" status so user gets feedback
    session = Session(
        session_id=session_id,
        product_name=product_name or "Unknown Product",
        platform=platform or "Web",
        status="scraping",
    )
    store.save_session(session)

    # Run Firecrawl agent in background — it can take minutes
    asyncio.create_task(_run_scrape(session_id, url, product_name, platform))

    # Redirect to chat page which shows a progress view
    return HTMLResponse(
        status_code=200,
        content=f'<script>window.location.href="/chat/{session_id}";</script>',
        headers={"HX-Redirect": f"/chat/{session_id}"},
    )


async def _run_scrape(session_id: str, url: str, product_name: str, platform: str):
    """Background task: scrape URL, index reviews, update session status."""
    try:
        reviews = await scrape_url(url, platform=platform)

        if not reviews:
            store.set_status(session_id, "error")
            await _emit(session_id, "No reviews could be extracted from that URL.", "error")
            return

        summary = build_summary(reviews, source_type="url", product_name=product_name, platform=platform)
        store.save_reviews_json(session_id, [r.model_dump(mode="json") for r in reviews])
        indexed = vectordb.index_reviews(session_id, reviews)
        summary.total_reviews = indexed
        store.update_summary(session_id, summary)
        store.set_status(session_id, "ready")
        await _emit(session_id, f"Scraping complete — {indexed} reviews indexed.", "info")

    except Exception as e:
        store.set_status(session_id, "error")
        await _emit(session_id, f"Scraping failed: {e}", "error")


# ── Session status polling (for scraping progress) ───────────────────

@app.get("/api/status/{session_id}")
async def get_status(session_id: str):
    session = store.load_session(session_id)
    if not session:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse({"status": session.status})


# ── Archive (delete) session ──────────────────────────────────────────

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    deleted = store.delete_session(session_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


# ── Report generation ────────────────────────────────────────────────

@app.get("/api/report/{session_id}")
async def get_report(session_id: str):
    """Return the accumulated report findings as structured JSON."""
    session = store.load_session(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    findings = store.get_findings(session_id)
    return JSONResponse({
        "product_name": session.product_name,
        "platform": session.platform,
        "findings": findings,
        "total_findings": sum(len(v) for v in findings.values()),
    })


@app.post("/chat/{session_id}/report")
async def generate_report(request: Request, session_id: str):
    """Trigger report generation by sending a report request to the agent."""
    session = store.load_session(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    # Treat this as a chat message asking for a report
    message = "Generate a comprehensive analysis report from everything we've discussed. Use the saved report findings and the report-structure knowledge file to compile a well-structured document."

    user_msg = ChatMessage(role="user", content="Generate report")
    store.append_message(session_id, user_msg)

    event = _get_response_event(session_id)
    event.clear()

    user_html = _render_message(user_msg)
    asyncio.create_task(_run_agent_and_respond(session_id, message, session))

    thinking_html = (
        '<div id="thinking-indicator" class="message assistant-message thinking">'
        '<div class="message-content">'
        '<div class="thinking-dots"><span></span><span></span><span></span></div>'
        '</div></div>'
    )
    return HTMLResponse(user_html + thinking_html)


# ── Send chat message ───────────────────────────────────────────────

@app.post("/chat/{session_id}/send")
async def send_message(
    request: Request,
    session_id: str,
    message: str = Form(...),
):
    session = store.load_session(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    user_msg = ChatMessage(role="user", content=message)
    store.append_message(session_id, user_msg)

    event = _get_response_event(session_id)
    event.clear()

    user_html = _render_message(user_msg)
    asyncio.create_task(_run_agent_and_respond(session_id, message, session))

    thinking_html = (
        '<div id="thinking-indicator" class="message assistant-message thinking">'
        '<div class="message-content">'
        '<div class="thinking-dots"><span></span><span></span><span></span></div>'
        '</div></div>'
    )
    return HTMLResponse(user_html + thinking_html)


async def _run_agent_and_respond(session_id: str, message: str, session: Session):
    try:
        history = store.get_messages(session_id)

        assistant_msg = await handle_message(
            session_id=session_id,
            user_message=message,
            conversation_history=history[:-1],
            summary=session.summary,
            emit_fn=_emit,
        )

        store.append_message(session_id, assistant_msg)
        html = _render_message(assistant_msg)
        _get_queue(session_id).append({"event": "message", "data": html})

    except Exception as e:
        error_msg = ChatMessage(
            role="assistant",
            content=f"Sorry, I encountered an error: {e}",
        )
        store.append_message(session_id, error_msg)
        html = _render_message(error_msg)
        _get_queue(session_id).append({"event": "message", "data": html})

    _get_response_event(session_id).set()


# ── SSE stream ───────────────────────────────────────────────────────

@app.get("/chat/{session_id}/stream")
async def chat_stream(session_id: str):
    async def event_generator():
        queue = _get_queue(session_id)
        event = _get_response_event(session_id)

        while True:
            while queue:
                item = queue.popleft()
                yield {"event": item["event"], "data": item["data"]}
                if item["event"] == "message":
                    yield {"event": "done", "data": ""}
                    return

            if event.is_set() and not queue:
                yield {"event": "done", "data": ""}
                return

            await asyncio.sleep(0.15)

    return EventSourceResponse(event_generator())


# ── HTML rendering helpers ───────────────────────────────────────────

def _render_citations(html: str, sources: list[dict[str, Any]]) -> str:
    """Replace [source:review_id] markers with clickable citation popovers."""
    if not sources:
        return html
    source_map = {s["id"]: s for s in sources}

    def _replace(match):
        review_id = match.group(1)
        source = source_map.get(review_id)
        if not source:
            return match.group(0)
        text = html_module.escape(source.get("text", "")[:300])
        rating = source.get("rating", "")
        date = source.get("date", "")
        author = html_module.escape(source.get("author", "") or "Anonymous")
        meta_parts = [author]
        if rating:
            meta_parts.append(f"{rating}/5")
        if date:
            meta_parts.append(str(date)[:10])
        meta = " · ".join(meta_parts)
        return (
            f'<span class="citation" tabindex="0">'
            f'<span class="citation-marker">[source]</span>'
            f'<span class="citation-popover">'
            f'<span class="citation-text">"{text}"</span>'
            f'<span class="citation-meta">{meta}</span>'
            f'</span></span>'
        )

    return re.sub(r'\[source:([^\]]+)\]', _replace, html)


def _render_message(msg: ChatMessage) -> str:
    # Skip system-initiated trigger messages (auto-analysis)
    if msg.system_initiated and msg.role == "user":
        return ""

    role_class = "user-message" if msg.role == "user" else "assistant-message"
    escaped = html_module.escape(msg.content)

    if msg.role == "assistant":
        content_html = markdown.markdown(
            msg.content,
            extensions=["tables", "fenced_code"],
        )
        content_html = _render_citations(content_html, msg.sources)
    else:
        content_html = f"<p>{escaped}</p>"

    parts = [f'<div class="message {role_class}">']
    parts.append(f'<div class="message-content">{content_html}</div>')

    # Tool activity accordion
    if msg.role == "assistant" and msg.tool_calls:
        n = len(msg.tool_calls)
        parts.append('<details class="tool-accordion">')
        parts.append(
            f'<summary class="tool-accordion-header">'
            f'<svg class="tool-accordion-chevron" width="12" height="12" viewBox="0 0 24 24" '
            f'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">'
            f'<polyline points="6 9 12 15 18 9"/></svg>'
            f' {n} tool call{"s" if n != 1 else ""}</summary>'
        )
        parts.append('<div class="tool-accordion-body">')
        for tc in msg.tool_calls:
            tool_label = tc.tool_name.replace("_", " ").title()
            parts.append('<div class="tool-call-item">')
            parts.append(f'<span class="tool-call-name">{html_module.escape(tool_label)}</span>')
            parts.append(f'<span class="tool-call-summary">{html_module.escape(tc.summary)}</span>')
            if tc.inputs:
                detail_parts = []
                for k, v in tc.inputs.items():
                    if k in ("query", "operation", "chart_type", "title", "section", "name", "question", "keyword") and v:
                        detail_parts.append(f'{k}: {html_module.escape(str(v))}')
                if detail_parts:
                    parts.append(f'<span class="tool-call-detail">{" · ".join(detail_parts)}</span>')
            parts.append('</div>')
        parts.append('</div></details>')

    # Charts with data table toggle
    for i, chart in enumerate(msg.charts):
        chart_id = f"chart-{uuid.uuid4().hex[:8]}"
        data_id = f"data-{chart_id}"
        parts.append(f'<div class="chart-container">')
        parts.append(f'<canvas id="{chart_id}"></canvas>')
        parts.append(f'<script>renderChart("{chart_id}", {json.dumps(chart)});</script>')

        # Data table toggle
        labels = chart.get("data", {}).get("labels", [])
        datasets = chart.get("data", {}).get("datasets", [])
        if labels and datasets:
            parts.append(
                f'<button class="chart-data-toggle" '
                f"onclick=\"toggleChartData('{data_id}')\">View data</button>"
            )
            parts.append(f'<div class="chart-data-table" id="{data_id}" style="display:none">')
            parts.append('<table><thead><tr><th></th>')
            for ds in datasets:
                parts.append(f'<th>{html_module.escape(ds.get("label", ""))}</th>')
            parts.append('</tr></thead><tbody>')
            for j, label in enumerate(labels):
                parts.append(f'<tr><td>{html_module.escape(str(label))}</td>')
                for ds in datasets:
                    data = ds.get("data", [])
                    val = data[j] if j < len(data) else ""
                    parts.append(f'<td>{val}</td>')
                parts.append('</tr>')
            parts.append('</tbody></table></div>')

        parts.append('</div>')

    # Follow-up buttons
    if msg.follow_ups:
        parts.append('<div class="follow-ups">')
        for q in msg.follow_ups:
            escaped_q = html_module.escape(q)
            parts.append(
                f'<button class="follow-up-btn" onclick="sendFollowUp(this)" '
                f'data-question="{escaped_q}">{escaped_q}</button>'
            )
        parts.append('</div>')

    parts.append('</div>')
    return "\n".join(parts)
