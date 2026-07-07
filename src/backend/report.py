"""
PDF report generation for the NSO AI-PC Fitting web UI.

Produces a self-contained clinical report (patient info first, then results),
built from the same engine output as the on-screen prediction. Rule-based —
the report carries the "not clinically validated" disclaimer.
"""

from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

CYAN = colors.HexColor("#3ba6f1")
INK = colors.HexColor("#0c0a09")
WARM = colors.HexColor("#78716c")
ASH = colors.HexColor("#a8a29e")
BORDER = colors.HexColor("#e8e6e5")
FILL = colors.HexColor("#f5f5f4")

_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=17, textColor=INK, leading=20, spaceAfter=2)
_SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=9, textColor=WARM, leading=12, spaceAfter=2)
_CAP = ParagraphStyle("cap", fontName="Helvetica-Bold", fontSize=8.5, textColor=WARM, spaceBefore=10, spaceAfter=4)
_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, textColor=INK, leading=13)
_SMALL = ParagraphStyle("small", fontName="Helvetica", fontSize=8, textColor=ASH, leading=11)


def _kv(rows, widths=(72 * mm, 96 * mm)):
    t = Table(rows, colWidths=list(widths))
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
        ("TEXTCOLOR", (0, 0), (0, -1), WARM),
        ("TEXTCOLOR", (1, 0), (1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _grid(header, rows, widths, highlight_row=None):
    data = [header] + rows
    t = Table(data, colWidths=list(widths))
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), WARM),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("BACKGROUND", (0, 0), (-1, 0), FILL),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]
    if highlight_row is not None:
        r = highlight_row + 1  # account for header
        style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#e6f1fb")))
        style.append(("FONT", (0, r), (-1, r), "Helvetica-Bold", 9))
    t.setStyle(TableStyle(style))
    return t


def _doc(buf, title):
    return SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm, topMargin=15 * mm, bottomMargin=14 * mm,
        title=title,
    )


def _header(story, title, subtitle):
    badge = Table([["  Rule-based  "]], colWidths=[28 * mm])
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#e6f1fb")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#0c447c")),
        ("FONT", (0, 0), (-1, -1), "Helvetica-Bold", 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    row = Table([[Paragraph(title, _H1), badge]], colWidths=[128 * mm, 40 * mm])
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(row)
    story.append(Paragraph(subtitle, _SUB))
    story.append(Paragraph(f"Generated {date.today().isoformat()}", _SMALL))


_DISCLAIMER = (
    "This report is produced by a rule-based engine using fixed deterministic "
    "formulas — no model has been trained on clinical data. Probabilities and scores are "
    "concept estimates for demonstration only and are <b>not a clinically validated outcome</b>. "
    "Not intended for diagnosis or prescription."
)


_CASE_LABELS = {
    "A": "High control / High adaptation",
    "B": "High control / Low adaptation",
    "C": "Low control / High adaptation",
    "D": "Low control / Low adaptation",
}


def build_report(inputs, r, followup=None):
    """Single report. With ``followup`` it becomes the prediction report plus a
    follow-up section appended at the end (used by the follow-up screen)."""
    buf = BytesIO()
    doc = _doc(buf, "NSO AI-PC Fitting Report")
    s = []
    subtitle = ("Initial fitting prediction, with a follow-up visit appended."
                if followup else
                "Initial fitting prediction for the entered patient profile.")
    _header(s, "NSO AI-PC Fitting Report", subtitle)

    s.append(Paragraph("PATIENT INPUT", _CAP))
    s.append(_kv([
        ["Age", f"{inputs['age']:g} years"],
        ["Axial length", f"{inputs['al']:g} mm"],
        ["Spherical equivalent", f"{inputs['se']:g} D"],
        ["Photopic pupil", f"{inputs['pupil']:g} mm"],
        ["Near work", f"{inputs['near_hours']:g} h/day"],
        ["Outdoor time", f"{inputs['outdoor_hours']:g} h/day"],
        ["Comfort tolerance", f"{inputs['comfort']:g} / 100"],
        ["CSF quality", f"{inputs['csf']:g} / 100"],
    ]))
    if inputs.get("sa_strength") is not None or inputs.get("density") is not None:
        s.append(Paragraph(
            f"Design tuning override — SA strength: {inputs.get('sa_strength', 'default')}, "
            f"density: {inputs.get('density', 'default')}.", _SMALL))

    s.append(Paragraph("RECOMMENDED FITTING", _CAP))
    s.append(_kv([
        ["Recommended profile", str(r["profile"])],
        ["Expected AL reduction", f"{r['expected_al_reduction_mm_per_year']:.2f} mm/year"],
        ["Recommended follow-up", str(r["recommended_follow_up"])],
    ]))

    case = r["recommended_case"]
    s.append(Paragraph("PREDICTED OUTCOME", _CAP))
    s.append(_kv([
        ["Predicted case", f"Case {case} — {_CASE_LABELS[case]}"],
        ["Case probability", f"{round(r['quadrant_probabilities'][case] * 100)}%"],
        ["Control probability", f"{round(r['control_probability'] * 100)}%"],
        ["Adaptation probability", f"{round(r['adaptation_probability'] * 100)}%"],
    ]))

    s.append(Paragraph("DESIGN QUALITY, CONFIDENCE & ACTION", _CAP))
    s.append(_kv([
        ["Entropy / robustness probability", f"{round(r['entropy_robustness_probability'] * 100)}%"],
        ["Prediction confidence", f"{r['prediction_confidence']:.0f}%"],
        ["Recommended action", str(r["recommended_action"])],
    ]))

    s.append(Paragraph("TOP CONTRIBUTORS", _CAP))
    crows = [[c["factor"], ("+" if c["percent"] >= 0 else "−") + f"{abs(c['percent'])}%"]
             for c in r["top_contributors"]]
    s.append(_grid(["Factor", "Signed share"], crows, [120 * mm, 48 * mm]))

    if followup:
        ctx, fr = followup["ctx"], followup["result"]
        sign = "+" if fr["delta_al"] >= 0 else "−"
        s.append(PageBreak())
        s.append(Paragraph("FOLLOW-UP VISIT", _CAP))
        s.append(_kv([
            ["Baseline AL", f"{ctx['baseline_al']:g} mm"],
            ["Follow-up AL", f"{ctx['followup_al']:g} mm"],
            ["Follow-up interval", f"{ctx['interval_months']:g} months"],
            ["Delta AL", f"{sign}{abs(fr['delta_al']):.3f} mm"],
            ["Annualized delta AL", f"{sign}{abs(fr['annualized_delta_al']):.3f} mm/year"],
            ["Progression recommendation", str(fr["advice"])],
            ["Next recommended profile", str(fr["next_profile"])],
        ]))

    s.append(Spacer(1, 10))
    s.append(Paragraph(_DISCLAIMER, _SMALL))
    doc.build(s)
    return buf.getvalue()
