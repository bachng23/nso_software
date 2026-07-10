"""
NSO AI-PC Fitting v0.3 — computation engine (Streamlit-free).

All the deterministic rule-based logic lives here so it can be imported by
the FastAPI backend and the tests without pulling in Streamlit. The Streamlit
UI in nso_mvp.py imports everything from this module.
"""

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Configuration and constants
# --------------------------------------------------------------------------- #

# Total loss weights. The values sum to 1.0.
LOSS_WEIGHTS = {
    "AL": 0.35,  # axial-length control
    "MTF": 0.20,  # visual quality preservation
    "CSF": 0.15,  # contrast sensitivity
    "Comfort": 0.15,  # comfort tolerance
    "Safety": 0.10,  # glare and pupil-size safety
    "Manufacturing": 0.05,  # manufacturing feasibility
}

# Fixed NSO profile candidates.
NSO_PROFILES = {
    "Low": {
        "SA": "1-3-2D",
        "temporal": 1.10,
        "density": "Low",
        "density_numeric": 33,  # 0-100 scale for the neural indices
        "sa_strength": 3.0,  # representative peak SA add (D)
        "mtf_reduction": 0.15,
        "control_power": 0.35,
        "comfort_penalty": 0.10,
        "safety_penalty": 0.05,
        "manufacturing_penalty": 0.05,
    },
    "Medium": {
        "SA": "3-5-4D",
        "temporal": 1.25,
        "density": "Medium",
        "density_numeric": 66,
        "sa_strength": 5.0,
        "mtf_reduction": 0.25,
        "control_power": 0.55,
        "comfort_penalty": 0.20,
        "safety_penalty": 0.10,
        "manufacturing_penalty": 0.10,
    },
    "High": {
        "SA": "5-8-6D",
        "temporal": 1.40,
        "density": "High",
        "density_numeric": 100,
        "sa_strength": 8.0,
        "mtf_reduction": 0.35,
        "control_power": 0.75,
        "comfort_penalty": 0.35,
        "safety_penalty": 0.20,
        "manufacturing_penalty": 0.18,
    },
}


# --------------------------------------------------------------------------- #
# Computation helpers
# --------------------------------------------------------------------------- #

def norm(x: float, low: float, high: float) -> float:
    """Normalize x to [0, 1] over the interval [low, high]."""
    return float(np.clip((x - low) / (high - low), 0.0, 1.0))


def compute_baseline_risk(age, al, myopia, near_hours, pupil, outdoor_hours) -> float:
    """Estimate baseline myopia-progression risk before NSO intervention."""
    risk_age = 1 - norm(age, 6, 18)
    risk_al = norm(al, 22.5, 26.5)
    risk_myopia = norm(abs(myopia), 1, 8)
    risk_near = norm(near_hours, 1, 10)
    risk_pupil = norm(pupil, 3.0, 6.5)
    protect_outdoor = norm(outdoor_hours, 0, 4)

    baseline = (
        0.25 * risk_age
        + 0.25 * risk_al
        + 0.20 * risk_myopia
        + 0.15 * risk_near
        + 0.10 * risk_pupil
        - 0.10 * protect_outdoor
    )
    return float(np.clip(baseline, 0.0, 1.0))


def evaluate_profiles(baseline_risk, risk_csf, comfort_risk, risk_pupil) -> pd.DataFrame:
    """Evaluate each NSO profile and return a DataFrame sorted by total loss."""
    w = LOSS_WEIGHTS
    rows = []
    for name, p in NSO_PROFILES.items():
        patient_modifier = (
            0.65
            + 0.45 * baseline_risk
            + 0.08 * risk_pupil
            - 0.08 * risk_csf
            - 0.06 * comfort_risk
        )
        predicted_control = p["control_power"] * np.clip(patient_modifier, 0.60, 1.15)

        l_al = baseline_risk * (1 - p["control_power"])
        l_mtf = abs(p["mtf_reduction"] - 0.25)
        l_csf = risk_csf * p["mtf_reduction"]
        l_comfort = comfort_risk + p["comfort_penalty"]
        l_safety = risk_pupil * p["safety_penalty"]
        l_manufacturing = p["manufacturing_penalty"]

        l_total = (
            w["AL"] * l_al
            + w["MTF"] * l_mtf
            + w["CSF"] * l_csf
            + w["Comfort"] * l_comfort
            + w["Safety"] * l_safety
            + w["Manufacturing"] * l_manufacturing
        )

        rows.append(
            {
                "Profile": name,
                "SA": p["SA"],
                "Temporal": p["temporal"],
                "Density": p["density"],
                "DensityNumeric": p["density_numeric"],
                "SAStrength": p["sa_strength"],
                "MTF Reduction": f"{int(p['mtf_reduction'] * 100)}%",
                "Profile Control Power": round(p["control_power"] * 100, 1),
                "Predicted Control Index": round(predicted_control * 100, 1),
                "Loss": round(l_total, 4),
            }
        )

    return pd.DataFrame(rows).sort_values("Loss").reset_index(drop=True)


def next_profile_from_al(annualized_delta_al: float, current_profile: str):
    """Recommend NSO strength adjustment from annualized axial-length change."""
    # Round to 3 dp before comparing: floating-point noise (e.g. 24.55 - 24.50
    # -> 0.05000000000000071) can otherwise push an exact-boundary value like
    # 0.10 just over the threshold and misclassify it by one tier.
    annualized_delta_al = round(annualized_delta_al, 3)

    if annualized_delta_al <= 0.10:
        return ("Maintain current NSO profile", current_profile)

    if annualized_delta_al <= 0.20:
        advice = "Consider increasing one level"
        if current_profile == "Low":
            return (advice, "Medium")
        return (advice, "High")

    return (
        "Escalate NSO strength and schedule clinical review",
        "High",
    )


# --------------------------------------------------------------------------- #
# NSO Neural Optical Indices (0-100 each)
# --------------------------------------------------------------------------- #

def entropy_score(density, temporal_multiplier, sa_strength, zone_count=3) -> float:
    """Optical 'disorder' of the design: density, temporal asymmetry, SA, zones."""
    raw = (
        0.35 * density
        + 0.25 * (temporal_multiplier - 1.0) * 100
        + 0.30 * sa_strength * 10
        + 0.10 * zone_count * 10
    )
    return max(0.0, min(raw, 100.0))


def neural_adaptation(age, comfort_tolerance, csf_quality, entropy) -> float:
    """How well the visual system is expected to adapt to the design."""
    raw = (
        0.35 * comfort_tolerance
        + 0.30 * csf_quality
        + 0.20 * max(0, 100 - entropy)
        + 0.15 * max(0, 100 - age * 2)
    )
    return max(0.0, min(raw, 100.0))


def visual_stress(entropy, sa_strength, pupil_mm, comfort_tolerance) -> float:
    """Predicted visual discomfort / glare load."""
    raw = (
        0.35 * entropy
        + 0.25 * sa_strength * 10
        + 0.25 * max(0, pupil_mm - 4.0) * 15
        + 0.15 * (100 - comfort_tolerance)
    )
    return max(0.0, min(raw, 100.0))


def dynamic_robustness(
    zone_smoothness, decentration_tolerance, pupil_stability, manufacturing_tolerance
) -> float:
    """Tolerance to decentration, pupil variation, and manufacturing spread."""
    raw = (
        0.30 * zone_smoothness
        + 0.25 * decentration_tolerance
        + 0.25 * pupil_stability
        + 0.20 * manufacturing_tolerance
    )
    return max(0.0, min(raw, 100.0))


def responder_prediction(control_index, adaptation_score, entropy):
    """Composite Good/Moderate/Poor responder classification (0-100 score)."""
    score = 0.5 * control_index + 0.25 * adaptation_score + 0.25 * entropy
    score = max(0.0, min(score, 100.0))

    if score >= 75:
        tier = "Good"
        detail = {
            "al_reduction": "55~75%",
            "adaptation": "Excellent",
            "follow_up": "6 months",
            "note": "",
        }
    elif score >= 55:
        tier = "Moderate"
        detail = {
            "al_reduction": "30~55%",
            "adaptation": "Acceptable",
            "follow_up": "3 months",
            "note": "",
        }
    else:
        tier = "Poor"
        detail = {
            "al_reduction": "<30%",
            "adaptation": "Adaptation difficulty",
            "follow_up": "3 months",
            "note": (
                "Increase SA / increase temporal asymmetry / consider profile upgrade"
            ),
        }
    return score, tier, detail


# --------------------------------------------------------------------------- #
# V2 Dual-Path Prediction
#
# Two independent dimensions instead of a single Good/Moderate/Poor responder:
#   - Control Score    : predicted myopia-control efficacy
#   - Adaptation Score : predicted visual acceptance / neural adaptation
# combined into a 2x2 clinical decision matrix.
#
# NOTE: the V2 spec defines the INPUTS and OUTPUTS of each path but not the
# exact weights/thresholds. The coefficients below are reasonable placeholders
# and are flagged for clinical review (not calibrated).
# --------------------------------------------------------------------------- #

# Score >= this is treated as "High" for the 2x2 decision matrix.
DUAL_PATH_HIGH_THRESHOLD = 60.0


def control_score(age, al, near_hours, outdoor_hours, profile_strength) -> float:
    """Predicted myopia-control efficacy (0-100).

    profile_strength is the profile control power on a 0-100 scale.
    """
    age_factor = 1 - norm(age, 6, 18)            # younger eyes respond better
    al_factor = norm(al, 22.5, 26.5)             # longer AL -> stronger indication
    near_factor = norm(near_hours, 1, 10)        # more near work -> higher demand
    outdoor_factor = norm(outdoor_hours, 0, 4)   # outdoor time is protective

    raw = (
        0.50 * profile_strength
        + 0.20 * al_factor * 100
        + 0.15 * age_factor * 100
        + 0.10 * near_factor * 100
        - 0.10 * outdoor_factor * 100
    )
    return max(0.0, min(raw, 100.0))


def adaptation_score(
    pupil_mm, entropy, visual_stress_index, neural_adaptation_score, comfort
) -> float:
    """Predicted visual acceptance / neural adaptation capability (0-100)."""
    pupil_factor = norm(pupil_mm, 3.0, 6.5)      # larger pupil -> harder adaptation

    raw = (
        0.35 * neural_adaptation_score
        + 0.25 * comfort
        + 0.20 * (100 - visual_stress_index)
        + 0.10 * (100 - entropy)
        + 0.10 * (100 - pupil_factor * 100)
    )
    return max(0.0, min(raw, 100.0))


def classify_high_low(score) -> str:
    """Binary classification used by the 2x2 decision matrix."""
    return "High" if score >= DUAL_PATH_HIGH_THRESHOLD else "Low"


def al_reduction_estimate(control_score_value) -> str:
    """Predicted axial-length reduction band from the control score."""
    if control_score_value >= 75:
        return "55~75%"
    if control_score_value >= 50:
        return "30~55%"
    return "<30%"


def adaptation_risk(adaptation_score_value) -> str:
    """Adaptation risk band from the adaptation score."""
    if adaptation_score_value >= 60:
        return "Low"
    if adaptation_score_value >= 40:
        return "Moderate"
    return "High"


def dual_path_decision(control_class, adaptation_class):
    """Map (Control, Adaptation) High/Low classes to a 2x2 matrix case.

    Returns (case_letter, case_title, [recommendations]).
    """
    matrix = {
        ("High", "High"): (
            "A",
            "Ideal candidate.",
            ["Immediate prescription", "Standard follow-up protocol"],
        ),
        ("High", "Low"): (
            "B",
            "Strong myopia-control efficacy but reduced adaptation potential.",
            [
                "Profile downshift",
                "Reduce SA (spherical aberration)",
                "Gradual adaptation strategy",
                "Enhanced patient counseling",
            ],
        ),
        ("Low", "High"): (
            "C",
            "Comfortable visual experience but limited myopia-control efficacy.",
            [
                "Increase microstructure density",
                "Increase SA strength",
                "Upgrade to a stronger NSO profile",
            ],
        ),
        ("Low", "Low"): (
            "D",
            "Suboptimal candidate for the current profile.",
            [
                "Alternative NSO profile",
                "Orthokeratology",
                "Atropine therapy",
                "Combination treatment strategy",
            ],
        ),
    }
    return matrix[(control_class, adaptation_class)]


# Short labels for each case, used by the 2x2 grid.
DUAL_PATH_SHORT_LABELS = {
    "A": "Ideal candidate",
    "B": "Strong control · low adaptation",
    "C": "Comfortable · low control",
    "D": "Suboptimal candidate",
}


def dual_path_grid(active_control_class, active_adaptation_class):
    """Lay out the 2x2 decision matrix as structured data.

    Control is the x-axis (Low | High), Adaptation the y-axis (High on top).
    Returns rows top-to-bottom, each a list of cells left-to-right, where each
    cell is a dict with case, control_class, adaptation_class, label, active.
    """
    layout = [
        [("Low", "High"), ("High", "High")],  # top row    -> Adaptation: High
        [("Low", "Low"), ("High", "Low")],    # bottom row -> Adaptation: Low
    ]
    grid = []
    for row in layout:
        cells = []
        for c_class, a_class in row:
            case = dual_path_decision(c_class, a_class)[0]
            cells.append(
                {
                    "case": case,
                    "control_class": c_class,
                    "adaptation_class": a_class,
                    "label": DUAL_PATH_SHORT_LABELS[case],
                    "active": (
                        c_class == active_control_class
                        and a_class == active_adaptation_class
                    ),
                }
            )
        grid.append(cells)
    return grid


# Each 2x2 case corresponds to a fixed (Control, Adaptation) class pair.
CASE_CLASSES = {
    "A": ("High", "High"),
    "B": ("High", "Low"),
    "C": ("Low", "High"),
    "D": ("Low", "Low"),
}


def matrix_quadrant_probabilities(p_control, p_adaptation):
    """Probability of each 2x2 case from the Control/Adaptation probabilities.

    Treats the two paths as independent, so the four quadrants form a joint
    distribution that always sums to 1.0 — the probability-driven replacement
    for the hard High/Low classification.
    """
    return {
        "A": p_control * p_adaptation,
        "B": p_control * (1.0 - p_adaptation),
        "C": (1.0 - p_control) * p_adaptation,
        "D": (1.0 - p_control) * (1.0 - p_adaptation),
    }


def recommended_case(quadrant_probs):
    """The most probable case (argmax of the quadrant distribution)."""
    return max(quadrant_probs, key=quadrant_probs.get)


# --------------------------------------------------------------------------- #
# V3 Probabilistic AI Responder Prediction
#
# Per supervisor feedback, the discrete High/Low + single-label output is
# wrapped in a probabilistic layer that mimics the shape of a future ML model:
#   - scores -> probabilities (calibrated sigmoid)
#   - 3-component responder score (Control + Adaptation + Entropy)
#   - Good/Moderate/Poor as probabilities
#   - prediction confidence
#   - explainable-AI top contributors
#
# IMPORTANT: every output below is still produced by deterministic rules, not a
# trained model. It is a placeholder shaped so that an XGBoost (or logistic)
# model can replace the internals later with little UI change. The sigmoid
# uses a centre/scale so a 0-100 score maps sensibly to a probability (a plain
# sigmoid(score) would saturate to ~100% for any score above ~6).
# --------------------------------------------------------------------------- #

SIGMOID_CENTER = 50.0   # score that maps to 50% probability
SIGMOID_SCALE = 12.0    # larger -> gentler curve
RESPONDER_PROB_SCALE = 8.0  # spread of the Good/Moderate/Poor ordered logit
RESPONDER_GOOD_THRESHOLD = 75.0
RESPONDER_POOR_THRESHOLD = 55.0


def _sigmoid(x) -> float:
    return float(1.0 / (1.0 + np.exp(-x)))


def score_to_probability(score, center=SIGMOID_CENTER, scale=SIGMOID_SCALE) -> float:
    """Calibrated sigmoid: maps a 0-100 score to a probability in (0, 1).

    center maps to 0.5; scale controls steepness. This is the standard logistic
    with location/scale parameters (the form used by logistic regression and
    temperature scaling) — NOT a plain sigmoid(score), which would saturate.
    """
    return _sigmoid((score - center) / scale)


def responder_score_v3(control, adaptation, entropy) -> float:
    """3-component responder score (0-100): efficacy + persistence + design."""
    raw = 0.45 * control + 0.35 * adaptation + 0.20 * entropy
    return max(0.0, min(raw, 100.0))


def responder_probabilities(
    score,
    good_threshold=RESPONDER_GOOD_THRESHOLD,
    poor_threshold=RESPONDER_POOR_THRESHOLD,
    scale=RESPONDER_PROB_SCALE,
):
    """Good/Moderate/Poor probabilities via a cumulative ordered logit.

    Returns a dict that always sums to 1.0. This is the standard way to turn one
    ordinal score into class probabilities and matches the existing 75/55
    thresholds; an ML classifier would output the same three numbers.
    """
    p_good = score_to_probability(score, center=good_threshold, scale=scale)
    p_not_poor = score_to_probability(score, center=poor_threshold, scale=scale)
    p_moderate = max(0.0, p_not_poor - p_good)
    p_poor = max(0.0, 1.0 - p_not_poor)
    return {"Good": p_good, "Moderate": p_moderate, "Poor": p_poor}


# Typical input ranges used as a stand-in for a model's "training range".
TYPICAL_RANGES = {
    "age": (6, 16),
    "al": (22.5, 26.5),
    "pupil": (3.0, 6.5),
    "near_hours": (0, 10),
    "comfort": (40, 100),
    "csf": (40, 100),
}


LOW_DESIGN_THRESHOLD = 0.5  # entropy/robustness probability below this = weak design


def entropy_robustness_probability(entropy, robustness):
    """Design-quality / stability probability (0-1) from the entropy and
    dynamic-robustness indices. Not a matrix axis — it gates confidence and
    the recommended action (per supervisor's v0.3 spec)."""
    design_quality = 0.5 * entropy + 0.5 * robustness
    return score_to_probability(design_quality)


def prediction_confidence(
    age, al, pupil, near_hours, comfort, csf, probabilities, er_probability
):
    """Heuristic prediction confidence (0-100) and a 'need more data' flag.

    Combines (a) inputs inside the typical range, (b) how peaked the
    Good/Moderate/Poor distribution is, and (c) the entropy/robustness
    (design-quality) probability, which pulls confidence down when the design
    is weak. A real model would replace this with a calibrated uncertainty.
    """
    values = {
        "age": age,
        "al": al,
        "pupil": pupil,
        "near_hours": near_hours,
        "comfort": comfort,
        "csf": csf,
    }
    in_range = [
        TYPICAL_RANGES[k][0] <= v <= TYPICAL_RANGES[k][1] for k, v in values.items()
    ]
    coverage = sum(in_range) / len(in_range)
    peak = max(probabilities.values())
    confidence = 100.0 * (0.4 * coverage + 0.3 * peak + 0.3 * er_probability)
    confidence = max(0.0, min(confidence, 100.0))
    low_design = er_probability < LOW_DESIGN_THRESHOLD
    need_more = confidence < 70.0 or coverage < 1.0 or low_design
    return round(confidence, 1), need_more


# Concise clinical action per most-likely case.
CASE_ACTIONS = {
    "A": "Proceed to prescription; standard follow-up.",
    "B": "Downshift the profile, reduce SA, and counsel the patient.",
    "C": "Strengthen the profile (density / SA) to lift control.",
    "D": "Consider an alternative therapy (Ortho-K, atropine, combination).",
}


def recommended_action(rec_case, er_probability, need_more):
    """Single recommended-action line, gated by design quality then confidence."""
    if er_probability < LOW_DESIGN_THRESHOLD:
        return (
            "Low entropy/robustness — adjust the design (SA / density) and "
            "schedule closer follow-up before prescribing."
        )
    if need_more:
        return (
            "Confidence limited (inputs outside typical range) — collect more "
            "follow-up data before deciding."
        )
    return CASE_ACTIONS[rec_case]


def top_contributors(
    age, al, near_hours, outdoor_hours, profile_strength, entropy, adaptation, top_n=5
):
    """Explainable-AI contributors: signed effect of each factor on the
    responder score, relative to a neutral patient (percentage points).

    Because the model is linear, the centred weighted terms ARE the exact
    contributions — this is the honest explanation of the rule-based score.
    """
    age_factor = 1 - norm(age, 6, 18)
    al_factor = norm(al, 22.5, 26.5)
    near_factor = norm(near_hours, 1, 10)
    outdoor_factor = norm(outdoor_hours, 0, 4)

    contributions = {
        "Age": 0.45 * 0.15 * 100 * (age_factor - 0.5),
        "AL": 0.45 * 0.20 * 100 * (al_factor - 0.5),
        "Near Work": 0.45 * 0.10 * 100 * (near_factor - 0.5),
        "Outdoor": -0.45 * 0.10 * 100 * (outdoor_factor - 0.5),
        "Profile Strength": 0.45 * 0.50 * (profile_strength - 50),
        "Entropy": 0.20 * (entropy - 50),
        "Adaptation": 0.35 * (adaptation - 50),
    }
    # Express each factor as a signed share (%) of the total influence, so the
    # numbers read like "this factor accounts for +X% of the decision".
    total = sum(abs(v) for v in contributions.values()) or 1.0
    shares = {name: 100.0 * value / total for name, value in contributions.items()}
    ranked = sorted(shares.items(), key=lambda kv: abs(kv[1]), reverse=True)
    return [
        {"factor": name, "percent": round(value)} for name, value in ranked[:top_n]
    ]


def expected_al_reduction_mm(control, baseline_progression=0.30) -> float:
    """Estimated axial-length reduction in mm/year from the control score.

    Assumes an untreated progression of ~baseline_progression mm/year and that
    the strongest profiles achieve up to ~75% reduction.
    """
    fraction = max(0.0, min(control / 100.0, 1.0)) * 0.75
    return round(baseline_progression * fraction, 2)


# --------------------------------------------------------------------------- #
# Orchestration — one call runs the whole pipeline and returns a plain dict.
# Shared by the Streamlit UI and the FastAPI backend so both use identical logic.
# --------------------------------------------------------------------------- #

def run_prediction(
    age,
    al,
    myopia,
    pupil,
    near_hours,
    outdoor_hours,
    csf_score,
    comfort_score,
    sa_strength=None,
    density=None,
):
    """Full initial-fitting prediction. sa_strength/density default to the
    selected profile's values but can be overridden (advanced design tuning)."""
    risk_pupil = norm(pupil, 3.0, 6.5)
    risk_csf = 1 - norm(csf_score, 40, 100)
    comfort_risk = 1 - norm(comfort_score, 40, 100)

    baseline_risk = compute_baseline_risk(
        age, al, myopia, near_hours, pupil, outdoor_hours
    )
    df = evaluate_profiles(baseline_risk, risk_csf, comfort_risk, risk_pupil)
    best = df.iloc[0]

    sa = float(best["SAStrength"]) if sa_strength is None else float(sa_strength)
    dens = float(best["DensityNumeric"]) if density is None else float(density)

    entropy = entropy_score(
        density=dens, temporal_multiplier=best["Temporal"], sa_strength=sa
    )
    adaptation = neural_adaptation(age, comfort_score, csf_score, entropy)
    stress = visual_stress(entropy, sa, pupil, comfort_score)
    robustness = dynamic_robustness(85, 80, 75, 90)

    profile_strength = float(best["Profile Control Power"])
    ctrl_score = control_score(age, al, near_hours, outdoor_hours, profile_strength)
    adapt_score = adaptation_score(pupil, entropy, stress, adaptation, comfort_score)

    p_control = score_to_probability(ctrl_score)
    p_adaptation = score_to_probability(adapt_score)
    quadrant = matrix_quadrant_probabilities(p_control, p_adaptation)
    rec_case = recommended_case(quadrant)
    case_letter, case_title, case_recs = dual_path_decision(*CASE_CLASSES[rec_case])

    er_probability = entropy_robustness_probability(entropy, robustness)
    resp_score = responder_score_v3(ctrl_score, adapt_score, entropy)
    probs = responder_probabilities(resp_score)
    confidence, need_more = prediction_confidence(
        age, al, pupil, near_hours, comfort_score, csf_score, probs, er_probability
    )
    action = recommended_action(rec_case, er_probability, need_more)
    contributors = top_contributors(
        age=age,
        al=al,
        near_hours=near_hours,
        outdoor_hours=outdoor_hours,
        profile_strength=profile_strength,
        entropy=entropy,
        adaptation=adapt_score,
    )
    follow_up = "6 months" if probs["Good"] >= 0.6 and not need_more else "3 months"

    return {
        "profile": best["Profile"],
        "sa_profile": best["SA"],
        "temporal_multiplier": best["Temporal"],
        "sa_strength": round(sa, 2),
        "density": round(dens, 1),
        "expected_al_reduction_mm_per_year": expected_al_reduction_mm(ctrl_score),
        "al_reduction_band": al_reduction_estimate(ctrl_score),
        "recommended_follow_up": follow_up,
        "control_score": round(ctrl_score, 1),
        "adaptation_score": round(adapt_score, 1),
        "control_probability": round(p_control, 4),
        "adaptation_probability": round(p_adaptation, 4),
        "control_class": classify_high_low(ctrl_score),
        "adaptation_class": classify_high_low(adapt_score),
        "quadrant_probabilities": {k: round(v, 4) for k, v in quadrant.items()},
        "recommended_case": rec_case,
        "case_title": case_title,
        "case_recommendations": case_recs,
        "entropy": round(entropy, 1),
        "robustness": round(robustness, 1),
        "entropy_robustness_probability": round(er_probability, 4),
        "responder_probabilities": {k: round(v, 4) for k, v in probs.items()},
        "prediction_confidence": confidence,
        "need_more_data": need_more,
        "recommended_action": action,
        "top_contributors": contributors,
    }


def run_followup(baseline_al, followup_al, interval_months, current_profile="Medium"):
    """Follow-up progression assessment from two axial-length readings."""
    interval_months = interval_months or 1
    delta = followup_al - baseline_al
    annualized = delta * (12 / interval_months)
    advice, next_profile = next_profile_from_al(annualized, current_profile)
    return {
        "delta_al": round(delta, 3),
        "annualized_delta_al": round(annualized, 3),
        "advice": advice,
        "next_profile": next_profile,
    }

