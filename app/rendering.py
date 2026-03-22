"""HTML rendering helpers for chat messages."""

from __future__ import annotations

import html as html_module
import json
import re
import uuid
from typing import Any

import markdown as md
from markupsafe import Markup

from .models import ChatMessage


def render_message_filter(msg: ChatMessage) -> Markup:
    """Jinja2 filter — renders a ChatMessage as safe HTML."""
    return Markup(render_message(msg))


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


def _render_chart_html(chart: dict[str, Any]) -> str:
    """Render a single chart as HTML (canvas + script + data table toggle)."""
    chart_id = f"chart-{uuid.uuid4().hex[:8]}"
    data_id = f"data-{chart_id}"
    parts = [f'<div class="chart-container">']
    parts.append(f'<canvas id="{chart_id}"></canvas>')
    parts.append(f'<script>renderChart("{chart_id}", {json.dumps(chart)});</script>')

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
    return "\n".join(parts)


def _render_inline_charts(html: str, charts: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Replace [chart:N] markers with rendered charts. Returns updated HTML and leftover charts."""
    if not charts:
        return html, []

    placed: set[int] = set()

    def _replace(match):
        idx = int(match.group(1))
        if idx < len(charts) and idx not in placed:
            placed.add(idx)
            return _render_chart_html(charts[idx])
        return ""  # Strip unmatched markers

    html = re.sub(r'\[chart:(\d+)\]', _replace, html)

    # Also handle markers that survived markdown (wrapped in <p> tags)
    def _replace_wrapped(match):
        idx = int(match.group(1))
        if idx < len(charts) and idx not in placed:
            placed.add(idx)
            return _render_chart_html(charts[idx])
        return ""  # Strip unmatched markers

    html = re.sub(r'<p>\[chart:(\d+)\]</p>', _replace_wrapped, html)

    leftovers = [c for i, c in enumerate(charts) if i not in placed]
    return html, leftovers


def _render_download_cards(html: str) -> str:
    """Replace report download links with styled download cards."""
    def _replace(match):
        url = match.group(1)
        return (
            f'<div class="report-download-card">'
            f'<div class="report-card-icon">'
            f'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">'
            f'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
            f'<polyline points="14 2 14 8 20 8"/>'
            f'<line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>'
            f'</svg></div>'
            f'<div class="report-card-body">'
            f'<span class="report-card-title">Analysis Report Ready</span>'
            f'<span class="report-card-desc">PDF report compiled and ready for download</span>'
            f'</div>'
            f'<a href="{url}" class="report-card-btn" download>Download PDF</a>'
            f'</div>'
        )
    # Match markdown-rendered links pointing to report download
    html = re.sub(
        r'<a href="(/api/report/[^"]+/download)"[^>]*>[^<]*</a>',
        _replace, html
    )
    # Also match raw URLs in text
    html = re.sub(
        r'(?<!")(\/api\/report\/[a-f0-9-]+\/download)(?!")',
        lambda m: _replace(m) if '<a' not in html[max(0,m.start()-30):m.start()] else m.group(0),
        html
    )
    return html


def render_message(msg: ChatMessage) -> str:
    """Render a ChatMessage as HTML string.

    Layout: thinking zone (collapsed accordion) → output zone (text with
    inline charts, citations, follow-ups).
    """
    # Skip system-initiated trigger messages (auto-analysis)
    if msg.system_initiated and msg.role == "user":
        return ""

    role_class = "user-message" if msg.role == "user" else "assistant-message"
    escaped = html_module.escape(msg.content)

    if msg.role == "assistant":
        content_html = md.markdown(
            msg.content,
            extensions=["tables", "fenced_code"],
        )
        content_html = _render_citations(content_html, msg.sources)
        content_html = _render_download_cards(content_html)
    else:
        content_html = f"<p>{escaped}</p>"

    parts = [f'<div class="message {role_class}">']

    # ── Timeline: thinking + tool calls interleaved (collapsed) ────
    if msg.role == "assistant" and msg.timeline:
        n = len(msg.timeline)
        parts.append('<details class="tool-accordion">')
        parts.append(
            f'<summary class="tool-accordion-header">'
            f'<svg class="tool-accordion-chevron" width="12" height="12" viewBox="0 0 24 24" '
            f'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">'
            f'<polyline points="6 9 12 15 18 9"/></svg>'
            f' {n} step{"s" if n != 1 else ""} — View analysis process</summary>'
        )
        parts.append('<div class="tool-accordion-body">')
        for step in msg.timeline:
            if step.type == "thinking" and step.text.strip():
                parts.append(
                    f'<div class="timeline-thinking">'
                    f'<p>{html_module.escape(step.text[:500])}</p>'
                    f'</div>'
                )
            elif step.type == "tool":
                tool_label = step.tool_name.replace("_", " ").title()
                parts.append('<div class="tool-call-item">')
                parts.append(f'<span class="tool-call-name">{html_module.escape(tool_label)}</span>')
                parts.append(f'<span class="tool-call-summary">{html_module.escape(step.summary)}</span>')
                if step.inputs:
                    detail_parts = []
                    for k, v in step.inputs.items():
                        if k in ("query", "operation", "chart_type", "title", "section", "name", "question", "keyword") and v:
                            detail_parts.append(f'{k}: {html_module.escape(str(v))}')
                    if detail_parts:
                        parts.append(f'<span class="tool-call-detail">{" · ".join(detail_parts)}</span>')
                parts.append('</div>')
        parts.append('</div></details>')

    # ── Output zone: text with inline charts ─────────────────────
    if msg.role == "assistant" and msg.charts:
        content_html, leftover_charts = _render_inline_charts(content_html, msg.charts)
    else:
        leftover_charts = []

    parts.append(f'<div class="message-content">{content_html}</div>')

    # Append any charts that weren't placed inline
    for chart in leftover_charts:
        parts.append(_render_chart_html(chart))

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
