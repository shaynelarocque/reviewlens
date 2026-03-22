"""PDF report generation using fpdf2 + matplotlib for charts."""

from __future__ import annotations

import re
import tempfile
from datetime import datetime
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from fpdf import FPDF

from .models import IngestionSummary

# ── Chart colors matching the app's teal palette ─────────────────────

COLORS = [
    "#2dd4bf", "#f59e0b", "#5eead4", "#f87171",
    "#818cf8", "#34d399", "#fbbf24",
]


def _render_chart_to_tempfile(chart: dict[str, Any]) -> str | None:
    """Render a chart config to a temp PNG file. Returns path or None."""
    chart_type = chart.get("type", "bar")
    title = chart.get("title", "")
    data = chart.get("data", {})
    labels = data.get("labels", [])
    datasets = data.get("datasets", [])

    if not labels or not datasets:
        return None

    fig, ax = plt.subplots(figsize=(7, 3.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    actual_type = chart_type
    if chart_type in ("horizontalBar", "stacked_bar"):
        actual_type = "bar"

    if actual_type == "bar":
        import numpy as np
        x = np.arange(len(labels))
        width = 0.8 / max(len(datasets), 1)
        for i, ds in enumerate(datasets):
            values = ds.get("data", [])
            color = COLORS[i % len(COLORS)]
            if chart_type == "horizontalBar":
                ax.barh(x + i * width, values, width, label=ds.get("label", ""), color=color)
            elif chart_type == "stacked_bar":
                bottom = [0] * len(labels)
                if i > 0:
                    for prev in datasets[:i]:
                        for j, v in enumerate(prev.get("data", [])):
                            if j < len(bottom):
                                bottom[j] += v
                ax.bar(x, values, 0.6, bottom=bottom, label=ds.get("label", ""), color=color)
            else:
                ax.bar(x + i * width, values, width, label=ds.get("label", ""), color=color)
        if chart_type == "horizontalBar":
            ax.set_yticks(x + width * (len(datasets) - 1) / 2)
            ax.set_yticklabels(labels, fontsize=8)
        else:
            ax.set_xticks(x + width * (len(datasets) - 1) / 2)
            ax.set_xticklabels(labels, fontsize=8, rotation=45, ha="right")
    elif actual_type == "line":
        for i, ds in enumerate(datasets):
            ax.plot(labels, ds.get("data", []), marker="o", markersize=4,
                    color=COLORS[i % len(COLORS)], label=ds.get("label", ""), linewidth=2)
        ax.tick_params(axis="x", rotation=45, labelsize=8)
    elif actual_type in ("pie", "doughnut"):
        values = datasets[0].get("data", []) if datasets else []
        colors = COLORS[:len(values)]
        wedgeprops = {"width": 0.4} if actual_type == "doughnut" else {}
        ax.pie(values, labels=labels, colors=colors, autopct="%1.0f%%",
               textprops={"fontsize": 8}, wedgeprops=wedgeprops)
    elif actual_type == "radar":
        import numpy as np
        angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
        angles += angles[:1]
        ax = fig.add_subplot(111, polar=True)
        for i, ds in enumerate(datasets):
            values = ds.get("data", []) + ds.get("data", [])[:1]
            ax.plot(angles, values, color=COLORS[i % len(COLORS)], linewidth=2, label=ds.get("label", ""))
            ax.fill(angles, values, color=COLORS[i % len(COLORS)], alpha=0.15)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, fontsize=8)
    elif actual_type == "scatter":
        for i, ds in enumerate(datasets):
            ax.scatter(range(len(ds.get("data", []))), ds.get("data", []),
                       color=COLORS[i % len(COLORS)], label=ds.get("label", ""), s=30)

    ax.set_title(title, fontsize=11, fontweight="500", pad=12)
    if len(datasets) > 1:
        ax.legend(fontsize=8)

    plt.tight_layout()
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return tmp.name


# ── Text sanitization ────────────────────────────────────────────────

_UNICODE_MAP = {
    "\u2014": "--", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00b7": " - ",
    "\u2022": "-", "\u2023": ">", "\u25cf": "-", "\u2192": "->",
    "\u2190": "<-", "\u2265": ">=", "\u2264": "<=", "\u00a0": " ",
    "\u2705": "[OK]", "\u274c": "[X]", "\u26a0": "[!]",
    "\u2b50": "*",
}

# Emoji severity indicators -> text badges
_EMOJI_BADGES = [
    (re.compile(r'[\U0001f534\u2b55]'), "[HIGH]"),     # red circle
    (re.compile(r'[\U0001f7e1\U0001f7e0]'), "[MED]"),  # yellow/orange circle
    (re.compile(r'[\U0001f7e2\u2705]'), "[LOW]"),       # green circle
    (re.compile(r'[\U0001f6a8]'), "[!]"),               # siren
    (re.compile(r'[\U0001f4ca\U0001f4c8\U0001f4c9]'), ""),  # chart emojis
    (re.compile(r'[\U0001f3c6\U0001f947\U0001f948\U0001f949]'), ""),  # medal emojis
    (re.compile(r'[\U0001f4cb\U0001f4dd\U0001f4d1]'), ""),  # clipboard/memo
    (re.compile(r'[\U0001f50d\U0001f50e]'), ""),        # magnifying glass
]


def _safe_text(text: str) -> str:
    """Replace Unicode characters with safe Latin-1 equivalents."""
    for pattern, replacement in _EMOJI_BADGES:
        text = pattern.sub(replacement, text)
    for k, v in _UNICODE_MAP.items():
        text = text.replace(k, v)
    # Strip any remaining non-latin1 chars
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _strip_md(text: str) -> str:
    """Strip markdown formatting for plain text output."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[source:[^\]]+\]', '', text)
    text = re.sub(r'\[chart:\d+\]', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    return _safe_text(text.strip())


# ── PDF class ────────────────────────────────────────────────────────

class ReportPDF(FPDF):
    """Custom PDF with ReviewLens branding."""

    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=22)
        self.set_margins(left=18, top=20, right=18)

    def header(self):
        if self.page_no() > 1:
            self.set_y(8)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(45, 212, 191)
            self.cell(0, 6, "ReviewLens AI", align="L")
            self.set_draw_color(45, 212, 191)
            self.set_line_width(0.3)
            y = self.get_y() + 7
            self.line(self.l_margin, y, self.w - self.r_margin, y)
            self.set_y(y + 4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(156, 163, 175)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


# ── PDF generation ───────────────────────────────────────────────────

def generate_pdf(
    title: str,
    content_md: str,
    summary: IngestionSummary,
    charts: list[dict[str, Any]] | None = None,
) -> bytes:
    """Generate a styled PDF report. Returns PDF bytes."""

    # Pre-render charts to temp files
    chart_files: list[str | None] = []
    if charts:
        for chart in charts:
            chart_files.append(_render_chart_to_tempfile(chart))

    # Clean title of pipe chars and other artifacts
    clean_title = _safe_text(title.replace("|", "-").replace("  ", " ").strip())

    pdf = ReportPDF()
    pdf.alias_nb_pages()

    # ── Cover page ───────────────────────────────────────────────────
    pdf.add_page()

    # Center the logo + title in the top portion
    pdf.ln(35)

    # Logo
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(107, 125, 153)
    logo_text = "ReviewLens"
    logo_w = pdf.get_string_width(logo_text)
    ai_w = pdf.get_string_width(" AI")
    total_logo_w = logo_w + ai_w
    pdf.set_x((pdf.w - total_logo_w) / 2)
    pdf.cell(logo_w, 12, "Review", new_x="END")
    pdf.set_text_color(45, 212, 191)
    pdf.cell(0, 12, "Lens", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(107, 125, 153)
    pdf.cell(0, 6, "AI", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(18)

    # Title in teal
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(45, 212, 191)
    pdf.multi_cell(0, 10, clean_title, align="C")

    pdf.ln(16)

    # Metadata box
    now = datetime.utcnow().strftime("%B %d, %Y")
    meta_items = [
        ("Product", summary.product_name),
        ("Platform", summary.platform),
        ("Reviews Analysed", str(summary.total_reviews)),
    ]
    if summary.date_range:
        meta_items.append(("Date Range", summary.date_range))
    if summary.average_rating:
        meta_items.append(("Average Rating", f"{summary.average_rating:.1f} / 5"))
    meta_items.append(("Report Generated", now))

    box_x = 35
    box_w = pdf.w - 70
    box_y = pdf.get_y()

    # Draw box border
    pdf.set_draw_color(229, 231, 235)
    pdf.set_line_width(0.4)
    line_h = 7
    box_h = len(meta_items) * line_h + 12
    pdf.rect(box_x, box_y, box_w, box_h)

    # Top teal accent line on the box
    pdf.set_draw_color(45, 212, 191)
    pdf.set_line_width(1.0)
    pdf.line(box_x, box_y, box_x + box_w, box_y)

    pdf.set_y(box_y + 6)
    for label, value in meta_items:
        pdf.set_x(box_x + 8)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(107, 125, 153)
        pdf.cell(40, line_h, _safe_text(label))
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(26, 35, 50)
        pdf.cell(0, line_h, _safe_text(value), new_x="LMARGIN", new_y="NEXT")

    # ── Content pages ────────────────────────────────────────────────
    pdf.add_page()
    _render_markdown_to_pdf(pdf, content_md, chart_files)

    # ── Final footer ─────────────────────────────────────────────────
    pdf.ln(10)
    pdf.set_draw_color(229, 231, 235)
    pdf.set_line_width(0.2)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(156, 163, 175)
    pdf.cell(0, 8, f"Generated by ReviewLens AI - {now}", align="C")

    return pdf.output()


# ── Markdown rendering ───────────────────────────────────────────────

def _render_markdown_to_pdf(pdf: ReportPDF, md_text: str, chart_files: list[str | None]) -> None:
    """Parse markdown text and render to PDF with formatting."""

    lines = md_text.split("\n")
    in_table = False
    table_rows: list[list[str]] = []
    content_w = pdf.w - pdf.l_margin - pdf.r_margin

    for line in lines:
        stripped = line.strip()

        # Empty lines
        if not stripped:
            if in_table and table_rows:
                _render_table(pdf, table_rows)
                table_rows = []
                in_table = False
            pdf.ln(3)
            continue

        # Horizontal rules
        if re.match(r'^-{3,}$', stripped) or re.match(r'^\*{3,}$', stripped):
            if in_table and table_rows:
                _render_table(pdf, table_rows)
                table_rows = []
                in_table = False
            pdf.ln(3)
            pdf.set_draw_color(45, 212, 191)
            pdf.set_line_width(0.3)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(5)
            continue

        # Chart markers
        chart_match = re.match(r'\[chart:(\d+)\]', stripped)
        if chart_match:
            idx = int(chart_match.group(1))
            if idx < len(chart_files) and chart_files[idx]:
                pdf.ln(4)
                pdf.image(chart_files[idx], x=pdf.l_margin, w=content_w)
                pdf.ln(4)
            continue

        # Table rows
        if "|" in stripped and not stripped.startswith("#"):
            cells = [c.strip() for c in stripped.split("|")]
            cells = [c for c in cells if c]
            if all(re.match(r'^[-:]+$', c) for c in cells):
                continue
            table_rows.append(cells)
            in_table = True
            continue
        elif in_table and table_rows:
            _render_table(pdf, table_rows)
            table_rows = []
            in_table = False

        # Headings
        if stripped.startswith("# "):
            pdf.ln(6)
            pdf.set_font("Helvetica", "B", 16)
            pdf.set_text_color(13, 17, 23)
            pdf.multi_cell(content_w, 8, _strip_md(stripped[2:]))
            pdf.set_draw_color(45, 212, 191)
            pdf.set_line_width(0.6)
            pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
            pdf.ln(4)
        elif stripped.startswith("## "):
            pdf.ln(5)
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(13, 17, 23)
            pdf.multi_cell(content_w, 7, _strip_md(stripped[3:]))
            pdf.ln(2)
        elif stripped.startswith("### "):
            pdf.ln(4)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(55, 65, 81)
            pdf.multi_cell(content_w, 6, _strip_md(stripped[4:]))
            pdf.ln(2)
        # Blockquotes
        elif stripped.startswith("> "):
            pdf.set_draw_color(45, 212, 191)
            pdf.set_line_width(0.6)
            x = pdf.get_x()
            y = pdf.get_y()
            pdf.set_x(x + 6)
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(55, 65, 81)
            pdf.multi_cell(content_w - 10, 5, _strip_md(stripped[2:]))
            pdf.line(x + 3, y, x + 3, pdf.get_y())
            pdf.ln(2)
        # List items
        elif stripped.startswith("- ") or stripped.startswith("* "):
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(26, 35, 50)
            pdf.set_x(pdf.l_margin + 4)
            pdf.cell(4, 5, "-")
            pdf.multi_cell(content_w - 8, 5, _strip_md(stripped[2:]))
            pdf.ln(1)
        elif re.match(r'^\d+\. ', stripped):
            num_match = re.match(r'^(\d+)\. (.*)', stripped)
            if num_match:
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(26, 35, 50)
                pdf.set_x(pdf.l_margin + 4)
                pdf.cell(6, 5, f"{num_match.group(1)}.")
                pdf.multi_cell(content_w - 10, 5, _strip_md(num_match.group(2)))
                pdf.ln(1)
        # Regular paragraph
        else:
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(26, 35, 50)
            pdf.multi_cell(content_w, 5, _strip_md(stripped))
            pdf.ln(2)

    # Flush remaining table
    if in_table and table_rows:
        _render_table(pdf, table_rows)


def _render_table(pdf: ReportPDF, rows: list[list[str]]) -> None:
    """Render a table with proper column sizing."""
    if not rows:
        return

    pdf.ln(3)
    n_cols = max(len(r) for r in rows)
    content_w = pdf.w - pdf.l_margin - pdf.r_margin

    # Calculate column widths based on content
    col_widths = [0.0] * n_cols
    for row in rows:
        for j, cell in enumerate(row):
            if j < n_cols:
                w = pdf.get_string_width(_strip_md(cell)) + 4
                col_widths[j] = max(col_widths[j], w)

    # Normalize to fit content width
    total = sum(col_widths)
    if total > 0:
        col_widths = [w / total * content_w for w in col_widths]
    else:
        col_widths = [content_w / n_cols] * n_cols

    for i, row in enumerate(rows):
        if i == 0:
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(107, 114, 128)
            pdf.set_fill_color(243, 244, 246)
            for j, cell in enumerate(row):
                w = col_widths[j] if j < len(col_widths) else col_widths[-1]
                pdf.cell(w, 6, _strip_md(cell)[:40], border=1, fill=True)
            pdf.ln()
        else:
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(26, 35, 50)
            for j, cell in enumerate(row):
                w = col_widths[j] if j < len(col_widths) else col_widths[-1]
                pdf.cell(w, 5.5, _strip_md(cell)[:50], border=1)
            pdf.ln()
    pdf.ln(3)
