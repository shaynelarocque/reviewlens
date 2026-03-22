"""File-based session and message persistence."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .models import ChatMessage, IngestionSummary, Session

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))


def _session_dir(session_id: str) -> Path:
    d = DATA_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Session lifecycle ────────────────────────────────────────────────

def save_session(session: Session) -> None:
    path = _session_dir(session.session_id) / "session.json"
    path.write_text(session.model_dump_json(indent=2))


def load_session(session_id: str) -> Session | None:
    path = _session_dir(session_id) / "session.json"
    if not path.exists():
        return None
    return Session.model_validate_json(path.read_text())


def set_status(session_id: str, status: str) -> None:
    session = load_session(session_id)
    if session:
        session.status = status
        save_session(session)


def update_summary(session_id: str, summary: IngestionSummary) -> None:
    session = load_session(session_id)
    if session:
        session.summary = summary
        session.product_name = summary.product_name
        session.platform = summary.platform
        save_session(session)


# ── Chat messages ────────────────────────────────────────────────────

def append_message(session_id: str, message: ChatMessage) -> None:
    session = load_session(session_id)
    if session:
        session.messages.append(message)
        save_session(session)


def get_messages(session_id: str) -> list[ChatMessage]:
    session = load_session(session_id)
    return session.messages if session else []


# ── Reviews (raw JSON for reference) ────────────────────────────────

def save_reviews_json(session_id: str, reviews: list[dict]) -> None:
    path = _session_dir(session_id) / "reviews.json"
    path.write_text(json.dumps(reviews, default=str, indent=2))


def load_reviews_json(session_id: str) -> list[dict]:
    path = _session_dir(session_id) / "reviews.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


# ── Session listing ──────────────────────────────────────────────────

def list_sessions() -> list[Session]:
    """Return all sessions, newest first."""
    sessions = []
    if not DATA_DIR.exists():
        return sessions
    for d in DATA_DIR.iterdir():
        if not d.is_dir():
            continue
        path = d / "session.json"
        if path.exists():
            try:
                sessions.append(Session.model_validate_json(path.read_text()))
            except Exception:
                continue
    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return sessions


def delete_session(session_id: str) -> bool:
    """Delete a session and all its data. Returns True if it existed."""
    d = DATA_DIR / session_id
    if d.exists() and d.is_dir():
        shutil.rmtree(d)
        return True
    return False
