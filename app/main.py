"""FastAPI application for ReviewLens AI."""

from __future__ import annotations

import asyncio
import csv
import io
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from . import knowledge, store, vectordb
from .agent import handle_message
from .ingest import build_summary, parse_csv, scrape_url
from .models import ChatMessage, Session
from .rendering import render_message, render_message_filter
from .sse import emit, get_queue, get_response_event

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
app = FastAPI(title="ReviewLens AI")

# Load knowledge base on startup
knowledge.load()
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["render_message"] = render_message_filter


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


async def _generate_workspace_name(session_id: str, reviews: list) -> None:
    """Use Haiku to generate a concise workspace name from review samples."""
    try:
        import anthropic

        sample_texts = [r.get("text", r.text if hasattr(r, "text") else "")[:150] for r in reviews[:8]]
        sample_block = "\n---\n".join(sample_texts)

        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=30,
            messages=[{
                "role": "user",
                "content": (
                    f"Based on these review samples, generate a short workspace name (2-5 words) "
                    f"that captures what's being reviewed. Examples: 'Bella Napoli Reviews', "
                    f"'AirPods Pro Feedback', 'Hilton Downtown Analysis'. Return ONLY the name.\n\n{sample_block}"
                ),
            }],
        )
        name = resp.content[0].text.strip().strip('"\'')
        if name and len(name) < 60:
            session = store.load_session(session_id)
            if session:
                session.product_name = name
                session.summary.product_name = name
                store.save_session(session)
    except Exception:
        pass  # Keep the original name on failure


def _trigger_auto_analysis(session_id: str, session: Session) -> None:
    """Save a synthetic message and kick off the initial analysis agent run."""
    trigger_msg = ChatMessage(
        role="user",
        content="[initial_analysis]",
        system_initiated=True,
    )
    store.append_message(session_id, trigger_msg)
    get_response_event(session_id).clear()
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
    return templates.TemplateResponse(request, "app.html", context=_shell_context(request))


@app.get("/chat/{session_id}", response_class=HTMLResponse)
async def chat_page(request: Request, session_id: str):
    session = store.load_session(session_id)
    if not session:
        return HTMLResponse("<h1>Session not found</h1>", status_code=404)
    return templates.TemplateResponse(request, "app.html", context=_shell_context(request, session))


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
            request,
            "partials/error.html",
            context={"error": f"Failed to parse CSV: {e}"},
            status_code=400,
        )

    if not reviews:
        return templates.TemplateResponse(
            request,
            "partials/error.html",
            context={"error": "No reviews found in the CSV. Make sure it has a text/review column."},
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

    # Name the workspace and kick off auto-analysis
    await _generate_workspace_name(session_id, reviews)
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
            request,
            "partials/error.html",
            context={"error": "Sample file not found."},
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
            request,
            "partials/error.html",
            context={"error": "Could not parse reviews from this sample file."},
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

    # Name the workspace and kick off auto-analysis
    await _generate_workspace_name(session_id, reviews)
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
            await emit(session_id, "No reviews could be extracted from that URL.", "error")
            return

        summary = build_summary(reviews, source_type="url", product_name=product_name, platform=platform)
        store.save_reviews_json(session_id, [r.model_dump(mode="json") for r in reviews])
        indexed = vectordb.index_reviews(session_id, reviews)
        summary.total_reviews = indexed
        store.update_summary(session_id, summary)
        store.set_status(session_id, "ready")
        await emit(session_id, f"Scraping complete — {indexed} reviews indexed.", "info")

        # Name the workspace and kick off auto-analysis
        await _generate_workspace_name(session_id, reviews)
        session = store.load_session(session_id)
        if session:
            _trigger_auto_analysis(session_id, session)

    except Exception as e:
        store.set_status(session_id, "error")
        await emit(session_id, f"Scraping failed: {e}", "error")


# ── Session status polling (for scraping progress) ───────────────────

@app.get("/api/status/{session_id}")
async def get_status(session_id: str):
    session = store.load_session(session_id)
    if not session:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse({"status": session.status})


# ── Report PDF download ──────────────────────────────────────────────

@app.get("/api/report/{session_id}/download")
async def download_report(session_id: str):
    """Serve the generated PDF report."""
    session = store.load_session(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    from pathlib import Path
    report_path = Path(store._session_dir(session_id)) / "report.pdf"
    if not report_path.exists():
        return JSONResponse({"error": "No report generated yet"}, status_code=404)

    filename = f"{session.product_name or 'report'}_report.pdf".replace(" ", "_")
    return StreamingResponse(
        open(report_path, "rb"),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── CSV download ─────────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}/csv")
async def download_csv(session_id: str):
    """Download the session's reviews as a CSV file."""
    session = store.load_session(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    reviews = store.load_reviews_json(session_id)
    if not reviews:
        return JSONResponse({"error": "No reviews found"}, status_code=404)

    # Collect all metadata keys across reviews for columns
    meta_keys: list[str] = []
    seen: set[str] = set()
    for r in reviews:
        for k in r.get("metadata", {}):
            if k not in seen:
                seen.add(k)
                meta_keys.append(k)

    buf = io.StringIO()
    writer = csv.writer(buf)

    # Header
    columns = ["text"] + meta_keys
    writer.writerow(columns)

    # Rows
    for r in reviews:
        meta = r.get("metadata", {})
        row = [r.get("text", "")]
        for k in meta_keys:
            row.append(meta.get(k, ""))
        writer.writerow(row)

    buf.seek(0)
    filename = f"{session.product_name or 'reviews'}.csv".replace(" ", "_")
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    event = get_response_event(session_id)
    event.clear()

    user_html = render_message(user_msg)
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

    event = get_response_event(session_id)
    event.clear()

    user_html = render_message(user_msg)
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
            emit_fn=emit,
        )

        store.append_message(session_id, assistant_msg)
        html = render_message(assistant_msg)
        get_queue(session_id).append({"event": "message", "data": html})

    except Exception as e:
        error_msg = ChatMessage(
            role="assistant",
            content=f"Sorry, I encountered an error: {e}",
        )
        store.append_message(session_id, error_msg)
        html = render_message(error_msg)
        get_queue(session_id).append({"event": "message", "data": html})

    get_response_event(session_id).set()


# ── SSE stream ───────────────────────────────────────────────────────

@app.get("/chat/{session_id}/stream")
async def chat_stream(session_id: str):
    async def event_generator():
        queue = get_queue(session_id)
        event = get_response_event(session_id)

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

    return EventSourceResponse(event_generator(), ping=15)
