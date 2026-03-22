"""Pydantic models for ReviewLens."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Review(BaseModel):
    """A single review record."""

    id: str = ""
    text: str
    rating: float | None = None
    date: datetime | None = None
    author: str = ""
    platform: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestionSummary(BaseModel):
    """Summary shown after ingestion."""

    total_reviews: int = 0
    date_range: str = ""
    rating_distribution: dict[str, int] = Field(default_factory=dict)
    average_rating: float | None = None
    platform: str = ""
    product_name: str = ""
    source_type: str = ""  # "csv" or "url"


class ToolCallRecord(BaseModel):
    """A single tool invocation record for the activity accordion."""

    tool_name: str
    summary: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    """A single chat message."""

    role: str  # "user" or "assistant"
    content: str
    charts: list[dict[str, Any]] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Session(BaseModel):
    """A review analysis session."""

    session_id: str
    product_name: str = ""
    platform: str = ""
    summary: IngestionSummary = Field(default_factory=IngestionSummary)
    messages: list[ChatMessage] = Field(default_factory=list)
    report_findings: dict[str, list[str]] = Field(default_factory=dict)
    status: str = "pending"  # pending, ready, error
    created_at: datetime = Field(default_factory=datetime.utcnow)
