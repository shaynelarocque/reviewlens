"""FastAPI application for ReviewLens AI."""

from __future__ import annotations

import asyncio
import html as html_module
import json
import os
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import markdown
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from . import store, vectordb
from .agent import handle_message
from .ingest import build_summary, parse_csv, scrape_url
from .models import ChatMessage, IngestionSummary, Session

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
app = FastAPI(title="ReviewLens AI")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

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
    ctx = {
        "request": request,
        "sessions": sessions,
        "session": session,
        "summary": session.summary if session else None,
        "messages": session.messages if session else [],
        "active_id": session.session_id if session else None,
        "sample_files": _list_sample_files(),
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

def _render_message(msg: ChatMessage) -> str:
    role_class = "user-message" if msg.role == "user" else "assistant-message"
    escaped = html_module.escape(msg.content)

    if msg.role == "assistant":
        content_html = markdown.markdown(
            msg.content,
            extensions=["tables", "fenced_code"],
        )
    else:
        content_html = f"<p>{escaped}</p>"

    parts = [f'<div class="message {role_class}">']
    parts.append(f'<div class="message-content">{content_html}</div>')

    for i, chart in enumerate(msg.charts):
        chart_id = f"chart-{uuid.uuid4().hex[:8]}"
        parts.append(
            f'<div class="chart-container">'
            f'<canvas id="{chart_id}"></canvas>'
            f'<script>renderChart("{chart_id}", {json.dumps(chart)});</script>'
            f'</div>'
        )

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
