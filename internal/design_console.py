"""
NSO internal design console — ENGINEERING USE ONLY.

NOT the clinician-facing product. This app displays the complete design recipe:
SA profile, microstructure density, temporal asymmetry, manufacturing export.
That is the Design IP the entire server-side architecture exists to keep off the
network (see ``src/backend/nso/__init__.py`` for the layer map).

It lives outside ``src/backend`` on purpose. The deploy root is that directory,
so nothing here can be packaged and shipped by accident -- labelling a file
"internal" does not stop a deploy, moving it out of the build does. A second
guard below refuses to start without ``NSO_INTERNAL_CONSOLE=1``.

Run on a trusted machine only:

    pip install -r internal/requirements.txt
    NSO_INTERNAL_CONSOLE=1 streamlit run internal/design_console.py

Pipeline:
    Clinical input -> baseline risk -> profile loss -> best NSO profile
                   -> manufacturing export -> AL follow-up -> strength adjustment
"""

import os
import sys
from pathlib import Path

# The engine lives in the deploy root; this console is a consumer of it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "backend"))

if os.environ.get("NSO_INTERNAL_CONSOLE") != "1":
    raise SystemExit(
        "Refusing to start: this console exposes the full design recipe.\n"
        "Set NSO_INTERNAL_CONSOLE=1 only on a trusted machine."
    )

import numpy as np
import pandas as pd
import streamlit as st

from nso_core import *  # noqa: F401,F403 — engine lives in nso_core


# --------------------------------------------------------------------------- #
# User interface
# --------------------------------------------------------------------------- #

def main() -> None:
    """Render the Streamlit fitting app. Kept inside a function so the pure
    computation helpers above can be imported (e.g. by tests) without executing
    any Streamlit UI on import."""
    st.set_page_config(page_title="NSO AI-PC Fitting MVP v0.3", layout="centered")

    st.title("NSO AI-PC Fitting MVP v0.3")
    st.caption("Rule-based Probabilistic Responder Engine")

    # ---------- Input ----------
    st.subheader("Clinical Input")
    age = st.slider("Age", 6, 18, 9)
    al = st.number_input("Axial Length (AL), mm", 20.0, 28.0, 24.5, 0.1)
    myopia = st.number_input("Spherical Equivalent, D", -12.0, 0.0, -3.00, 0.25)
    pupil = st.number_input("Photopic Pupil, mm", 2.5, 7.0, 4.5, 0.1)
    near_hours = st.slider("Near Work, hours/day", 0, 12, 5)
    outdoor_hours = st.slider("Outdoor Time, hours/day", 0, 6, 1)
    csf_score = st.slider("CSF Quality, 0-100", 0, 100, 75)
    comfort_score = st.slider("Comfort Tolerance, 0-100", 0, 100, 80)

    # ---------- Computation ----------
    risk_pupil = norm(pupil, 3.0, 6.5)
    risk_csf = 1 - norm(csf_score, 40, 100)
    comfort_risk = 1 - norm(comfort_score, 40, 100)

    baseline_risk = compute_baseline_risk(
        age, al, myopia, near_hours, pupil, outdoor_hours
    )
    df = evaluate_profiles(baseline_risk, risk_csf, comfort_risk, risk_pupil)
    best = df.iloc[0]

    # ---------- Output ----------
    st.subheader("Recommended NSO Profile")
    st.success(f"{best['Profile']} | SA: {best['SA']} | Temporal: {best['Temporal']}x")

    col1, col2 = st.columns(2)
    col1.metric("Predicted Control Index", f"{best['Predicted Control Index']}%")
    col2.metric("Total Loss", best["Loss"])

    st.subheader("Profile Comparison")
    st.dataframe(
        df.drop(columns=["DensityNumeric", "SAStrength"]),
        use_container_width=True,
    )

    # ---------- NSO Neural Optical Indices ----------
    st.subheader("NSO Neural Optical Indices")

    # Profile design parameters default to the selected profile but can be
    # tuned here to explore how they drive the indices. The slider key includes
    # the profile name so each profile keeps its own override.
    with st.expander(
        "Profile design tuning (SA strength / microstructure density)",
        expanded=True,
    ):
        sa_strength_val = st.slider(
            "SA strength (peak SA add, D)",
            0.0,
            12.0,
            float(best["SAStrength"]),
            0.5,
            key=f"sa_{best['Profile']}",
        )
        density_val = st.slider(
            "Microstructure density (0-100)",
            0,
            100,
            int(best["DensityNumeric"]),
            1,
            key=f"density_{best['Profile']}",
        )

    entropy = entropy_score(
        density=density_val,
        temporal_multiplier=best["Temporal"],
        sa_strength=sa_strength_val,
    )
    adaptation = neural_adaptation(
        age=age,
        comfort_tolerance=comfort_score,
        csf_quality=csf_score,
        entropy=entropy,
    )
    stress = visual_stress(
        entropy=entropy,
        sa_strength=sa_strength_val,
        pupil_mm=pupil,
        comfort_tolerance=comfort_score,
    )
    robustness = dynamic_robustness(
        zone_smoothness=85,
        decentration_tolerance=80,
        pupil_stability=75,
        manufacturing_tolerance=90,
    )

    icol1, icol2, icol3, icol4 = st.columns(4)
    icol1.metric("Entropy Score", f"{entropy:.1f}")
    icol2.metric("Neural Adaptation", f"{adaptation:.1f}")
    icol3.metric("Visual Stress", f"{stress:.1f}")
    icol4.metric("Dynamic Robustness", f"{robustness:.1f}")

    # Neural-index-based advisory profile suggestion (independent of the
    # loss-based recommendation above).
    myopia_risk = baseline_risk * 100
    if stress > 70:
        advisory_profile = "Low"
    elif entropy < 45 and myopia_risk > 60:
        advisory_profile = "Medium / High"
    elif adaptation < 50:
        advisory_profile = "Low-Medium"
    else:
        advisory_profile = "Medium"
    st.caption(f"Neural-index advisory profile: **{advisory_profile}**")

    # ---------- Dual-Path Prediction (V2) ----------
    st.subheader("Dual-Path Prediction")

    profile_strength = float(best["Profile Control Power"])
    ctrl_score = control_score(
        age=age,
        al=al,
        near_hours=near_hours,
        outdoor_hours=outdoor_hours,
        profile_strength=profile_strength,
    )
    adapt_score = adaptation_score(
        pupil_mm=pupil,
        entropy=entropy,
        visual_stress_index=stress,
        neural_adaptation_score=adaptation,
        comfort=comfort_score,
    )
    ctrl_class = classify_high_low(ctrl_score)
    adapt_class = classify_high_low(adapt_score)

    # Scores expressed as probabilities (calibrated sigmoid) — these drive the
    # probability version of the matrix below.
    p_control = score_to_probability(ctrl_score)
    p_adaptation = score_to_probability(adapt_score)

    dcol1, dcol2 = st.columns(2)
    dcol1.metric(
        "Control Score", f"{ctrl_score:.1f}",
        f"{p_control * 100:.0f}% probability", delta_color="off",
    )
    dcol2.metric(
        "Adaptation Score", f"{adapt_score:.1f}",
        f"{p_adaptation * 100:.0f}% probability", delta_color="off",
    )

    al_reduction = al_reduction_estimate(ctrl_score)
    adapt_risk = adaptation_risk(adapt_score)
    st.caption(
        f"Predicted AL reduction: **{al_reduction}**  ·  "
        f"Adaptation risk: **{adapt_risk}**"
    )

    # Probability-driven 2x2 matrix: each quadrant carries a probability (the
    # joint of the Control and Adaptation probabilities) instead of a hard cut.
    quadrant_probs = matrix_quadrant_probabilities(p_control, p_adaptation)
    rec_case = recommended_case(quadrant_probs)
    grid = dual_path_grid(ctrl_class, adapt_class)

    def _matrix_cell(cell):
        prob = quadrant_probs[cell["case"]]
        is_rec = cell["case"] == rec_case
        # Opaque blend light-grey -> accent blue by probability, so it reads on
        # both light and dark themes (a transparent fill would vanish on dark).
        base, acc = (238, 242, 246), (46, 117, 182)
        rgb = tuple(int(b + (a - b) * prob) for b, a in zip(base, acc))
        bg = f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"
        fg = "#FFFFFF" if prob >= 0.45 else "#1A2733"
        sub = "#E8EEF4" if prob >= 0.45 else "#5A6B7A"
        ring = "3px solid #1B4E7A" if is_rec else "1px solid #B9C4CE"
        marker = " &#9679;" if is_rec else ""
        return (
            f'<td style="border:{ring};background:{bg};color:{fg};'
            f'padding:12px;width:50%;vertical-align:top;border-radius:8px;">'
            f'<b>Case {cell["case"]}{marker}</b> &nbsp; '
            f'<span style="font-size:1.15em;font-weight:700;">{prob * 100:.0f}%</span>'
            f'<br><span style="font-size:0.85em;color:{sub};">{cell["label"]}</span></td>'
        )

    row_labels = ["Adaptation: High", "Adaptation: Low"]
    body_rows = ""
    for label, cells in zip(row_labels, grid):
        body_rows += (
            f'<tr><td style="font-size:0.8em;color:#888;writing-mode:vertical-rl;'
            f'transform:rotate(180deg);">{label}</td>'
            f'{_matrix_cell(cells[0])}{_matrix_cell(cells[1])}</tr>'
        )
    matrix_html = (
        '<table style="border-collapse:separate;border-spacing:6px;width:100%;'
        'text-align:center;font-family:Arial,sans-serif;">'
        '<tr><td></td>'
        '<td style="font-size:0.8em;color:#888;">Control: Low</td>'
        '<td style="font-size:0.8em;color:#888;">Control: High</td></tr>'
        f'{body_rows}'
        '</table>'
    )
    st.markdown("**2×2 Clinical Decision Matrix** (probability-driven)")
    st.markdown(matrix_html, unsafe_allow_html=True)
    st.write("")

    ctrl_cls_rec, adapt_cls_rec = CASE_CLASSES[rec_case]
    case, case_title, case_recs = dual_path_decision(ctrl_cls_rec, adapt_cls_rec)
    recs_md = "\n".join(f"- {r}" for r in case_recs)
    matrix_md = (
        f"**Most likely: Case {case} ({quadrant_probs[case] * 100:.0f}%)** — "
        f"{ctrl_cls_rec} Control / {adapt_cls_rec} Adaptation  \n"
        f"{case_title}\n\n"
        f"**Recommendation**  \n{recs_md}"
    )
    if case == "A":
        st.success(matrix_md)
    elif case in ("B", "C"):
        st.info(matrix_md)
    else:
        st.warning(matrix_md)

    # ---------- Design-quality gate + recommended action (v0.3) ----------
    # Entropy/Robustness is NOT a matrix axis (per supervisor): it is a separate
    # design-quality metric that modifies confidence and the recommended action.
    er_probability = entropy_robustness_probability(entropy, robustness)
    resp_score = responder_score_v3(ctrl_score, adapt_score, entropy)
    probs = responder_probabilities(resp_score)
    confidence, need_more = prediction_confidence(
        age, al, pupil, near_hours, comfort_score, csf_score, probs, er_probability
    )
    action = recommended_action(rec_case, er_probability, need_more)

    qcol1, qcol2 = st.columns(2)
    qcol1.metric("Entropy/Robustness Probability", f"{er_probability * 100:.0f}%")
    qcol2.metric("Prediction Confidence", f"{confidence:.0f}%")
    if er_probability < 0.5 or need_more:
        st.warning(f"**Recommended action:** {action}")
    else:
        st.info(f"**Recommended action:** {action}")

    # ---------- AI Responder Prediction (V3) ----------
    st.subheader("AI Responder Prediction")
    st.caption(
        "Probabilistic view — rule-based today, structured so an ML model "
        "(e.g. XGBoost) can replace the internals later."
    )

    # Probabilities (calibrated sigmoid) — computed above; shown here too.
    pcol1, pcol2 = st.columns(2)
    pcol1.metric("Control Probability", f"{p_control * 100:.0f}%")
    pcol2.metric("Adaptation Probability", f"{p_adaptation * 100:.0f}%")

    # Good/Moderate/Poor probabilities (computed above).
    gcol, mcol, pcol = st.columns(3)
    gcol.metric("Good", f"{probs['Good'] * 100:.0f}%")
    mcol.metric("Moderate", f"{probs['Moderate'] * 100:.0f}%")
    pcol.metric("Poor", f"{probs['Poor'] * 100:.0f}%")

    expected_mm = expected_al_reduction_mm(ctrl_score)
    follow_up = "6 months" if probs["Good"] >= 0.6 and not need_more else "3 months"
    ecol1, ecol2 = st.columns(2)
    ecol1.metric("Expected AL Reduction", f"{expected_mm:.2f} mm/year")
    ecol2.metric("Recommended Follow-up", follow_up)
    st.caption(f"Recommended profile: **{best['Profile']}**")

    # Explainable AI: top contributors to the responder score.
    contributors = top_contributors(
        age=age,
        al=al,
        near_hours=near_hours,
        outdoor_hours=outdoor_hours,
        profile_strength=profile_strength,
        entropy=entropy,
        adaptation=adapt_score,
    )
    st.markdown("**Top Contributors** (share of the prediction, %)")

    def _contrib_row(c):
        up = c["percent"] >= 0
        arrow = "&#9650;" if up else "&#9660;"  # ▲ / ▼
        color = "#1A9850" if up else "#D73027"  # green / red
        return (
            '<tr>'
            f'<td style="padding:4px 10px;">{c["factor"]}</td>'
            f'<td style="padding:4px 10px;text-align:right;color:{color};'
            f'font-weight:600;font-family:Arial,sans-serif;">'
            f'{arrow} {c["percent"]:+.0f}%</td>'
            '</tr>'
        )

    contrib_html = (
        '<table style="border-collapse:collapse;width:100%;'
        'font-family:Arial,sans-serif;font-size:0.95em;">'
        + "".join(_contrib_row(c) for c in contributors)
        + '</table>'
    )
    st.markdown(contrib_html, unsafe_allow_html=True)
    st.caption("▲ pushes the responder score up · ▼ pulls it down")

    # ---------- Export ----------
    st.subheader("Manufacturing Export")
    export = {
        "recommended_profile": best["Profile"],
        "SA_profile": best["SA"],
        "temporal_density_multiplier": best["Temporal"],
        "density_level": best["Density"],
        "target_MTF_reduction": best["MTF Reduction"],
        "baseline_risk_score": round(baseline_risk, 4),
        "total_loss": best["Loss"],
        "entropy_score": round(entropy, 1),
        "neural_adaptation_score": round(adaptation, 1),
        "visual_stress_index": round(stress, 1),
        "dynamic_robustness": round(robustness, 1),
        "control_score": round(ctrl_score, 1),
        "control_class": ctrl_class,
        "adaptation_score": round(adapt_score, 1),
        "adaptation_class": adapt_class,
        "predicted_AL_reduction": al_reduction,
        "adaptation_risk": adapt_risk,
        "decision_case": case,
        "case_probabilities": {k: round(v, 3) for k, v in quadrant_probs.items()},
        "control_probability": round(p_control, 3),
        "adaptation_probability": round(p_adaptation, 3),
        "responder_score_v3": round(resp_score, 1),
        "prob_good": round(probs["Good"], 3),
        "prob_moderate": round(probs["Moderate"], 3),
        "prob_poor": round(probs["Poor"], 3),
        "entropy_robustness_probability": round(er_probability, 3),
        "prediction_confidence": confidence,
        "recommended_action": action,
        "expected_AL_reduction_mm_per_year": expected_mm,
        "recommended_follow_up": follow_up,
        "top_contributors": "; ".join(
            f"{c['factor']} {c['percent']:+.0f}%" for c in contributors
        ),
    }
    st.json(export)

    csv = pd.DataFrame([export]).to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download NSO Prescription CSV",
        csv,
        "nso_prescription.csv",
        "text/csv",
    )

    # ---------- Follow-up AL tracking ----------
    st.divider()
    st.subheader("AL Follow-up Tracking And Strength Adjustment")

    baseline_al = st.number_input("Baseline AL, mm", 20.0, 28.0, al, 0.01)
    followup_al = st.number_input("Follow-up AL, mm", 20.0, 28.0, al + 0.05, 0.01)
    months = st.slider("Follow-up Interval, months", 1, 12, 3)

    delta_al = followup_al - baseline_al
    annualized_delta_al = delta_al * (12 / months)

    col3, col4 = st.columns(2)
    col3.metric("Delta AL", f"{delta_al:.3f} mm")
    col4.metric("Annualized Delta AL", f"{annualized_delta_al:.3f} mm/year")

    # ---------- Strength adjustment ----------
    adjustment, next_profile = next_profile_from_al(
        annualized_delta_al, best["Profile"]
    )
    st.warning(adjustment)
    st.info(f"Next Recommended Profile: {next_profile}")

    # ---------- Follow-up export ----------
    export.update(
        {
            "baseline_AL": round(baseline_al, 3),
            "followup_AL": round(followup_al, 3),
            "delta_AL": round(delta_al, 3),
            "annualized_delta_AL": round(annualized_delta_al, 3),
            "followup_interval_months": months,
            "adjustment_recommendation": adjustment,
            "next_recommended_profile": next_profile,
        }
    )
    csv2 = pd.DataFrame([export]).to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download Updated NSO Follow-up CSV",
        csv2,
        "nso_followup_prescription.csv",
        "text/csv",
    )


if __name__ == "__main__":
    main()
