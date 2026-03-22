"""In-memory SSE event queues and helpers."""

from __future__ import annotations

import asyncio
from collections import deque


_event_queues: dict[str, deque[dict[str, str]]] = {}
_response_events: dict[str, asyncio.Event] = {}


def get_queue(session_id: str) -> deque[dict[str, str]]:
    if session_id not in _event_queues:
        _event_queues[session_id] = deque()
    return _event_queues[session_id]


def get_response_event(session_id: str) -> asyncio.Event:
    if session_id not in _response_events:
        _response_events[session_id] = asyncio.Event()
    return _response_events[session_id]


async def emit(session_id: str, message: str, level: str = "info") -> None:
    get_queue(session_id).append({"event": level, "data": message})
