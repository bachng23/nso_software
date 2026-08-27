"""
PDF report generation for the NSO AI-PC Fitting web UI.

Clinical-layer report: patient input, visual phenotype, AI-derived indices and
predicted outcomes. It carries the Design ID, never the optical recipe — the
printed report is as IP-safe as the API response, since a PDF leaves the clinic
more easily than a browser session does. Rule-based, so the report also carries
the "not clinically validated" disclaimer.
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


_SUPPORT_HINT = {
    "Level 1": "baseline optical support",
    "Level 2": "intermediate optical support",
    "Level 3": "maximum optical support",
}


def _eye_row(inputs, key):
    e = inputs.get(key) or {}
    sph, cyl, axis = e.get("sphere", 0), e.get("cylinder", 0), e.get("axis", 0)
    return f"{sph:+.2f} / {cyl:+.2f} x {axis:g}\u00b0 \u00b7 AL {e.get('axial_length', 0):g} mm"


_INDEX_LABELS = [
    ("refractive_risk", "Refractive Risk Index"),
    ("binocular_load", "Binocular Load Index"),
    ("accommodative_stress", "Accommodative Stress Index"),
    ("spatial_frequency_sensitivity", "Spatial Frequency Sensitivity Index"),
    ("visual_stress", "Visual Stress Index"),
    ("neural_adaptation", "Neural Adaptation Index"),
    ("dynamic_robustness", "Dynamic Robustness Index"),
    ("interocular_image_balance", "Interocular Image-Balance Index"),
]

_PREDICTED_LABELS = [
    ("nso_control_score", "NSO Control Score (relative)"),
    ("visual_comfort", "Predicted visual comfort"),
    ("adaptation", "Predicted adaptation"),
    ("binocular_compatibility", "Predicted binocular compatibility"),
    ("dynamic_robustness", "Predicted dynamic robustness"),
]


def build_report(inputs, r, followup=None):
    """Single clinical report. With ``followup`` the prediction report gains a
    follow-up section at the end (used by the follow-up screen).

    ``r`` is the clinical payload from ``nso_v2.clinical_only`` — by
    construction it contains no design parameters, so there is nothing here to
    redact.
    """
    buf = BytesIO()
    doc = _doc(buf, "NSO AI-PC Fitting Report")
    s = []
    subtitle = ("Personalized optical design, with a follow-up visit appended."
                if followup else
                "Personalized optical design for the entered clinical profile.")
    _header(s, "NSO AI-PC Fitting Report", subtitle)

    s.append(Paragraph("CLINICAL INPUT", _CAP))
    s.append(_kv([
        ["Age", f"{inputs['age']:g} years"],
        ["Right eye (OD)", _eye_row(inputs, "od")],
        ["Left eye (OS)", _eye_row(inputs, "os")],
        ["Photopic pupil", f"{inputs['photopic_pupil']:g} mm"],
        ["Near phoria", f"{inputs['near_phoria']:g} \u0394"],
        ["NPC", f"{inputs['npc']:g} cm"],
        ["Accommodative lag", f"{inputs['accommodative_lag']:g} D"],
        ["Contrast sensitivity band", str(inputs["csf_band"])],
        ["Visual stress", f"{inputs['visual_stress_score']:g} / 10"],
        ["Near work / digital", f"{inputs['near_hours']:g} + {inputs['digital_hours']:g} h/day"],
        ["Outdoor time", f"{inputs['outdoor_hours']:g} h/day"],
        ["Primary optimization goal", str(inputs["primary_goal"])],
    ]))

    ph = r["phenotype"]
    s.append(Paragraph("INDIVIDUAL VISUAL PHENOTYPE", _CAP))
    s.append(Paragraph(f"Phenotype code: <b>{ph['code']}</b>", _BODY))
    s.append(Spacer(1, 4))
    s.append(_grid(
        ["Domain", "Score", "Grade"],
        [[d["name"], f"{d['score']:g}", d["grade_label"]] for d in ph["domains"]],
        [98 * mm, 34 * mm, 36 * mm],
    ))

    s.append(Paragraph("AI-DERIVED VISUAL INDICES", _CAP))
    s.append(_kv([[label, f"{r['indices'][key]:g} / 100"] for key, label in _INDEX_LABELS]))

    s.append(Paragraph("RECOMMENDED PERSONALIZED OPTICAL DESIGN", _CAP))
    s.append(_kv([
        ["NSO personalized design", str(r["design_id"])],
        ["Right eye (OD)", r["eyes"]["OD"]["profile_label"]],
        ["Left eye (OS)", r["eyes"]["OS"]["profile_label"]],
        ["Binocular pair optimization", str(r["binocular_pair"])],
        ["Manufacturing status", str(r["manufacturing_status"])],
    ]))
    s.append(Paragraph(
        "The optical design recipe is held server-side under the Design ID above and "
        "is not reproduced in this report.", _SMALL))

    s.append(Paragraph("PREDICTED PERFORMANCE", _CAP))
    s.append(_kv([[label, f"{r['predicted'][key]}%"] for key, label in _PREDICTED_LABELS]))

    s.append(Paragraph("SELECTION RATIONALE", _CAP))
    s.append(Paragraph(
        f"AI selected design: Candidate {r['selected_candidate']['OD']} (OD) / "
        f"{r['selected_candidate']['OS']} (OS). {r['selection_rationale']}", _BODY))
    s.append(Spacer(1, 5))
    for line in r["explainable_summary"]:
        s.append(Paragraph(f"\u2022 {line}", _BODY))

    s.append(Paragraph("CONFIDENCE & FOLLOW-UP", _CAP))
    s.append(_kv([
        ["Prediction confidence", f"{r['prediction_confidence']}%"],
        ["Recommended follow-up", str(r["recommended_follow_up"])],
        ["Measured optional domains",
         ", ".join(k.replace("_", " ") for k, v in r["measured_domains"].items() if v) or "none"],
    ]))

    if followup:
        ctx, fr = followup["ctx"], followup["result"]
        sign = "+" if fr["delta_al"] >= 0 else "\u2212"
        s.append(PageBreak())
        s.append(Paragraph("FOLLOW-UP VISIT", _CAP))
        s.append(_kv([
            ["Baseline AL", f"{ctx['baseline_al']:g} mm"],
            ["Follow-up AL", f"{ctx['followup_al']:g} mm"],
            ["Follow-up interval", f"{ctx['interval_months']:g} months"],
            ["Delta AL", f"{sign}{abs(fr['delta_al']):.3f} mm"],
            ["Annualized delta AL", f"{sign}{abs(fr['annualized_delta_al']):.3f} mm/year"],
            ["Progression band", str(fr["progression_band"])],
            ["Recommendation", str(fr["advice"])],
            ["Next support level",
             f"{fr['next_support_level']} \u2014 {_SUPPORT_HINT.get(fr['next_support_level'], '')}"],
            ["Refit required", "Yes" if fr["refit_required"] else "No"],
        ]))

    s.append(Spacer(1, 10))
    s.append(Paragraph(_DISCLAIMER, _SMALL))
    doc.build(s)
    return buf.getvalue()
