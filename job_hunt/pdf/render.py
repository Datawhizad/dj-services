"""ATS-friendly resume PDF rendering with ReportLab.

Layout: single column, standard sans-serif font, plain bullets — designed so
applicant tracking systems can extract text cleanly. No icons, no columns,
no graphics."""

from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "name": ParagraphStyle(
            "name",
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "contact", fontName="Helvetica", fontSize=10, leading=12, spaceAfter=10
        ),
        "section": ParagraphStyle(
            "section",
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#1a3050"),
            spaceBefore=10,
            spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", fontName="Helvetica", fontSize=10, leading=13, spaceAfter=4
        ),
        "company": ParagraphStyle(
            "company",
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            spaceBefore=6,
        ),
        "title_line": ParagraphStyle(
            "title_line",
            fontName="Helvetica-Oblique",
            fontSize=10,
            leading=12,
            spaceAfter=2,
        ),
        "bullet": ParagraphStyle(
            "bullet", fontName="Helvetica", fontSize=10, leading=13
        ),
    }


def _section_header(title: str, styles: dict) -> list:
    return [
        Paragraph(title.upper(), styles["section"]),
        HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#1a3050"), spaceAfter=4),
    ]


def _e(s: str | None) -> str:
    return escape(s or "")


def _sender_from_master_resume() -> tuple[str, str]:
    """Read sender name + contact line from master_resume.md so cover letters
    stay in sync with the resume header."""
    from ..ai.claude_client import get_master_resume

    lines = [l.strip() for l in get_master_resume().splitlines() if l.strip()]
    name = lines[0].lstrip("# ").strip() if lines else "Candidate"
    contact = lines[1] if len(lines) > 1 else ""
    return name, contact


def render_cover_letter(data: dict, out_path: Path) -> Path:
    """Render a structured cover-letter dict to a one-page business-letter PDF."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    body_para = ParagraphStyle(
        "letter_body",
        parent=styles["body"],
        fontSize=10.5,
        leading=14,
        spaceAfter=10,
    )
    name, contact = _sender_from_master_resume()

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=f"{name} — Cover Letter",
    )

    flow: list = [
        Paragraph(_e(name), styles["name"]),
        Paragraph(_e(contact), styles["contact"]),
        Paragraph(_e(data.get("date", "")), body_para),
    ]

    # Recipient block
    recipient_lines = []
    if data.get("recipient"):
        recipient_lines.append(_e(data["recipient"]))
    if data.get("company"):
        recipient_lines.append(_e(data["company"]))
    if data.get("company_address"):
        recipient_lines.append(_e(data["company_address"]))
    if recipient_lines:
        flow.append(Paragraph("<br/>".join(recipient_lines), body_para))

    # Greeting
    if data.get("greeting"):
        flow.append(Paragraph(_e(data["greeting"]), body_para))

    # Body
    for para in (data.get("body_paragraphs") or []):
        flow.append(Paragraph(_e(para), body_para))

    # Closing + signature line
    flow.append(Paragraph(_e(data.get("closing", "Sincerely,")), body_para))
    flow.append(Spacer(1, 28))
    flow.append(Paragraph(_e(data.get("signature_name", name)), body_para))

    doc.build(flow)
    return out_path


def render_resume(data: dict, out_path: Path) -> Path:
    """Render a structured resume dict (matching tailor_resume schema) to a PDF."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=f"{data.get('name', 'Resume')} — Resume",
    )

    flow: list = []

    # Header
    flow.append(Paragraph(_e(data.get("name", "")), styles["name"]))
    flow.append(Paragraph(_e(data.get("contact_line", "")), styles["contact"]))

    # Summary
    if data.get("summary"):
        flow.extend(_section_header("Professional Summary", styles))
        flow.append(Paragraph(_e(data["summary"]), styles["body"]))

    # Skills
    skills = data.get("skills") or []
    if skills:
        flow.extend(_section_header("Core Skills", styles))
        for cat in skills:
            cat_name = _e(cat.get("category", ""))
            items = ", ".join(_e(i) for i in (cat.get("items") or []))
            flow.append(Paragraph(f"<b>{cat_name}:</b> {items}", styles["body"]))

    # Experience
    exp = data.get("experience") or []
    if exp:
        flow.extend(_section_header("Experience", styles))
        for role in exp:
            block: list = []
            company = _e(role.get("company", ""))
            location = _e(role.get("location", ""))
            title = _e(role.get("title", ""))
            dates = _e(role.get("dates", ""))
            block.append(
                Paragraph(f"{company} &nbsp;—&nbsp; {location}", styles["company"])
            )
            block.append(Paragraph(f"<b>{title}</b> &nbsp;·&nbsp; {dates}", styles["title_line"]))
            bullets = role.get("bullets") or []
            if bullets:
                items = [
                    ListItem(Paragraph(_e(b), styles["bullet"]), leftIndent=10, bulletColor=colors.black)
                    for b in bullets
                ]
                block.append(
                    ListFlowable(
                        items,
                        bulletType="bullet",
                        bulletFontSize=8,
                        leftIndent=14,
                        bulletOffsetY=-1,
                    )
                )
            block.append(Spacer(1, 4))
            flow.append(KeepTogether(block))

    # Education
    edu = data.get("education") or []
    if edu:
        flow.extend(_section_header("Education & Certifications", styles))
        items = [
            ListItem(Paragraph(_e(e), styles["bullet"]), leftIndent=10) for e in edu
        ]
        flow.append(
            ListFlowable(items, bulletType="bullet", bulletFontSize=8, leftIndent=14)
        )

    doc.build(flow)
    return out_path
