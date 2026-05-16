"""PDF export for AI compliance reviews.

Uses reportlab's Platypus (SimpleDocTemplate + Paragraph + Table) to build
a structured, multi-page audit-ready PDF from a stored review row.

The PDF is generated in memory (BytesIO) so it streams directly to the
user via Streamlit's download_button — no temp files, no cleanup.
"""
from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from services.llm_review import extract_sections


RISK_COLORS = {
    "LOW":      colors.HexColor("#1f9d55"),  # green
    "MEDIUM":   colors.HexColor("#cb9425"),  # amber
    "HIGH":     colors.HexColor("#d35400"),  # orange
    "CRITICAL": colors.HexColor("#c0392b"),  # red
}


def _esc(text: str | None) -> str:
    """XML-escape user content and turn newlines into <br/> for Paragraph."""
    if text is None:
        return ""
    return xml_escape(str(text)).replace("\n", "<br/>")


def _build_styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"],
            fontSize=20, leading=24, spaceAfter=8,
            textColor=colors.HexColor("#1a1a1a"),
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            fontSize=13, leading=16, spaceBefore=10, spaceAfter=4,
            textColor=colors.HexColor("#1a1a1a"),
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"],
            fontSize=10, leading=14, spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "small", parent=base["BodyText"],
            fontSize=8, leading=11, textColor=colors.HexColor("#555555"),
        ),
        "mono": ParagraphStyle(
            "mono", parent=base["Code"],
            fontSize=8, leading=10,
        ),
    }


def build_review_pdf(review: dict) -> bytes:
    """Generate a PDF from a review dict.

    Required keys:
        id, created_at, model, prompt_name, prompt_version,
        risk_level, recommendation, raw_response,
        input_tokens, output_tokens, context_text, cost (dict|None)
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"AI Compliance Review #{review.get('id', '?')}",
        author="DKM compliance sandbox",
    )
    styles = _build_styles()
    story: list = []

    # ---- Title --------------------------------------------------------
    story.append(Paragraph("AI Compliance Review", styles["title"]))

    risk = (review.get("risk_level") or "").upper()
    risk_color = RISK_COLORS.get(risk, colors.HexColor("#666666"))
    story.append(Paragraph(
        f'<font color="{risk_color.hexval()}"><b>Risk Level: {_esc(risk or "—")}</b></font> '
        f'&nbsp;&nbsp;|&nbsp;&nbsp; '
        f'<b>Recommendation:</b> {_esc(review.get("recommendation") or "—")}',
        styles["body"],
    ))
    story.append(Spacer(1, 6 * mm))

    # ---- Metadata table ----------------------------------------------
    created = review.get("created_at")
    if hasattr(created, "strftime"):
        created_str = created.strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        created_str = str(created or "—")

    meta_rows = [
        ["Review ID:",  str(review.get("id", "—"))],
        ["Generated:",  created_str],
        ["Model:",      review.get("model") or "—"],
        ["Prompt:",     f'{review.get("prompt_name", "?")} ({review.get("prompt_version", "?")})'],
    ]
    meta_table = Table(meta_rows, colWidths=[35 * mm, 130 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME",     (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 6 * mm))

    # ---- Sections from raw response ----------------------------------
    raw = review.get("raw_response") or ""
    sections = extract_sections(raw)
    if "_full" in sections:
        story.append(Paragraph("Analysis", styles["h2"]))
        story.append(Paragraph(_esc(sections["_full"]), styles["body"]))
    else:
        ordered = [
            "Summary", "Risk Level", "Findings", "Sanctions Analysis",
            "Missing Information", "Recommendation", "Legal Basis",
        ]
        for title in ordered:
            body = sections.get(title, "").strip()
            if not body:
                continue
            story.append(Paragraph(_esc(title), styles["h2"]))
            story.append(Paragraph(_esc(body), styles["body"]))
            story.append(Spacer(1, 2 * mm))

    # ---- Usage & cost ------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Usage &amp; cost", styles["h2"]))

    it = int(review.get("input_tokens") or 0)
    ot = int(review.get("output_tokens") or 0)
    cost = review.get("cost") or {}

    usage_rows = [
        ["Input tokens",   f"{it:,}",   ""],
        ["Output tokens",  f"{ot:,}",   ""],
        ["Total tokens",   f"{it + ot:,}", ""],
    ]
    if cost:
        usage_rows.extend([
            ["Rate (input/output)",
             f"${cost.get('rate_in', 0):.2f} / ${cost.get('rate_out', 0):.2f} per MTok", ""],
            ["Input cost",   f"${cost.get('input_usd', 0):.6f}",  "USD"],
            ["Output cost",  f"${cost.get('output_usd', 0):.6f}", "USD"],
            ["Total cost",   f"${cost.get('total_usd', 0):.6f}",  "USD"],
            ["Total cost",   f"€{cost.get('total_eur', 0):.6f}",  "EUR (indicative)"],
        ])
    else:
        usage_rows.append(["Cost", "(pricing unknown for this model)", ""])

    usage_table = Table(usage_rows, colWidths=[50 * mm, 60 * mm, 50 * mm])
    usage_table.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("BACKGROUND",    (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (1, 0), (1, -1), "RIGHT"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
    ]))
    story.append(usage_table)
    story.append(Spacer(1, 6 * mm))

    # ---- Input context ----------------------------------------------
    ctx = (review.get("context_text") or "").strip()
    if ctx:
        story.append(Paragraph("Input context", styles["h2"]))
        # Cap to ~3000 chars to keep PDF reasonable
        if len(ctx) > 3000:
            ctx = ctx[:3000] + "\n\n[…truncated]"
        story.append(Paragraph(_esc(ctx), styles["body"]))
        story.append(Spacer(1, 4 * mm))

    # ---- Footer / disclaimer ----------------------------------------
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "<i>This document was generated by an AI compliance review system. "
        "The AI assessment is advisory and does not replace human compliance "
        "judgement. Cost estimates use Anthropic public API pricing "
        "(standard tier, no caching or batch discounts applied) and may differ "
        "from your actual billing.</i>",
        styles["small"],
    ))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · "
        f"DKM compliance sandbox",
        styles["small"],
    ))

    doc.build(story)
    return buf.getvalue()
