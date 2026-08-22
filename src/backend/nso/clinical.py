"""
The clinical layer — the only thing that crosses the network boundary.

Assembles the payload a clinician sees: Design ID, phenotype, indices,
predicted outcomes, and why this design was chosen. Every value here is an
outcome or an explanation; none of it is a design parameter.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List

import nso_core as engine

from .config import get_config
from .design import design_id_for, optimize_pair, pair_class
from .features import (
    accommodative_demand_d,
    ai_derived_indices,
    csf_descriptors,
    clip100,
    interocular_acuity_difference,
    out_of_range_measurements,
    plausibility,
    plausibly_measured_domains,
)
from .ip import assert_no_design_leak
from .manufacturing import REGISTRY
from .patient import PatientInput
from .phenotype import visual_phenotype


def _pct(x: float) -> int:
    return int(round(clip100(x)))


def _explainable_summary(
    p: PatientInput, phenotype, indices, pair_label, overruled=False,
    out_of_range=(),
) -> List[str]:
    """Plain-language 'why this design' — reasoning without the recipe."""
    lines = [
        f"Dominant driver: {phenotype['dominant_domain']} "
        f"(phenotype {phenotype['code']})."
    ]
    if indices["refractive_risk"] >= 60:
        lines.append(
            "High refractive risk (axial length and age) favours a stronger "
            "peripheral-defocus configuration."
        )
    if indices["visual_stress"] >= 60:
        lines.append(
            "Elevated visual stress caps the optical load to protect comfort and "
            "adaptation."
        )
    if indices["binocular_load"] >= 55:
        lines.append(
            "Binocular load (near phoria / NPC) shifts the design toward reduced "
            "near vergence demand."
        )
    if indices["accommodative_stress"] >= 55:
        lines.append(
            "Accommodative lag under sustained near work justifies additional near "
            "support."
        )
    if indices["spatial_frequency_sensitivity"] < 55:
        lines.append(
            "Reduced contrast sensitivity constrains the design to preserve "
            "high-spatial-frequency performance."
        )
    if pair_label != "Symmetric":
        lines.append(
            f"OD/OS designs are {pair_label.lower()} to respect interocular "
            "image balance."
        )
    if overruled:
        lines.append(
            "Joint optimization selected a pair that is not the best option for "
            "either eye alone, because the pair fuses better together."
        )
    if out_of_range:
        names = ", ".join(f["measurement"].replace("_", " ") for f in out_of_range)
        lines.append(
            f"Confidence reduced: {names} fall outside the range these rules were "
            "written for, so the prediction is an extrapolation."
        )
    lines.append(f"Primary optimization goal: {p.primary_goal}.")
    return lines


def run_fitting(p: PatientInput, site: str | None = None) -> Dict[str, Any]:
    """Full V2 fitting.

    Returns ``{"clinical": ..., "design": ...}``. Only ``clinical`` may be
    serialized to a client; ``design`` stays on the server and is registered
    under the Design ID for the manufacturing API.
    """
    cfg = get_config()
    indices = ai_derived_indices(p)
    phenotype = visual_phenotype(p, indices)

    # Joint optimization: the pair is selected together, so a monocularly
    # optimal design can lose to a slightly worse one that fuses better.
    pairs, od_candidates, os_candidates = optimize_pair(p, indices)
    best_pair = pairs[0]

    per_eye = {
        "OD": {"candidates": od_candidates, "selected": best_pair["od"]},
        "OS": {"candidates": os_candidates, "selected": best_pair["os"]},
    }
    od_recipe = best_pair["od"]["recipe"]
    os_recipe = best_pair["os"]["recipe"]
    pair_label = pair_class(od_recipe, os_recipe)
    design_id = design_id_for([od_recipe, os_recipe])

    od_metrics = per_eye["OD"]["selected"]["metrics"]
    os_metrics = per_eye["OS"]["selected"]["metrics"]

    def mean(key: str) -> float:
        return (od_metrics[key] + os_metrics[key]) / 2.0

    # Binocular compatibility reflects the penalty the optimizer actually paid
    # for this pair, not a label applied after the fact.
    binocular_compatibility = _pct(
        (0.6 * indices["interocular_image_balance"]
         + 0.4 * (100 - indices["binocular_load"]))
        * (1.0 - 0.45 * best_pair["binocular_penalty"])
    )

    # Confidence = how much was measured + whether it looks like a patient the
    # rules were written for. Coverage alone let an implausible measurement
    # raise confidence exactly as much as a normal one.
    measured = p.measured_domains()
    # Coverage counts only domains whose readings are plausible: an out-of-range
    # value has not informed that domain.
    credited = plausibly_measured_domains(p)
    coverage = sum(credited.values()) / len(credited)
    plausible = plausibility(p)
    out_of_range = out_of_range_measurements(p)
    confidence = _pct(
        cfg.confidence_floor
        + cfg.confidence_coverage_span * coverage
        + cfg.confidence_plausibility_span * plausible
    )

    overruled = (
        per_eye["OD"]["selected"] is not od_candidates[0]
        or per_eye["OS"]["selected"] is not os_candidates[0]
    )

    # Candidate comparison WITHOUT the recipe: clinicians see how the options
    # trade off, not what the options physically are.
    def candidate_rows(eye_key: str):
        return [
            {
                "candidate": c["label"],
                "predicted_control": c["metrics"]["control"],
                "predicted_comfort": c["metrics"]["comfort"],
                "predicted_adaptation": c["metrics"]["adaptation"],
                "predicted_acuity_retention": c["metrics"]["acuity"],
                "feasible": c["metrics"]["feasible"],
                "relative_loss": c["metrics"]["loss"],
                "selected": c is per_eye[eye_key]["selected"],
                "monocular_best": c is per_eye[eye_key]["candidates"][0],
            }
            for c in per_eye[eye_key]["candidates"]
        ]

    clinical = {
        "design_id": design_id,
        "phenotype": phenotype,
        "indices": indices,
        "eyes": {
            "OD": {"profile_label": "Personalized Functional Profile A"},
            "OS": {"profile_label": "Personalized Functional Profile B"},
        },
        "binocular_pair": pair_label,
        "predicted": {
            "myopia_control": _pct(mean("control")),
            "visual_comfort": _pct(mean("comfort")),
            "adaptation": _pct(mean("adaptation")),
            "binocular_compatibility": binocular_compatibility,
            "dynamic_robustness": _pct(indices["dynamic_robustness"]),
        },
        "candidate_comparison": {
            "OD": candidate_rows("OD"),
            "OS": candidate_rows("OS"),
        },
        "selected_candidate": {
            "OD": per_eye["OD"]["selected"]["label"],
            "OS": per_eye["OS"]["selected"]["label"],
        },
        # Joint optimization is visible as an outcome trade-off, never as the
        # parameters that were traded off.
        "joint_optimization": {
            "pairs_evaluated": len(pairs),
            "binocular_cost": round(best_pair["binocular_penalty"] * 100, 1),
            "overruled_monocular_choice": overruled,
        },
        "spatial_frequency_descriptors": csf_descriptors(p),
        "interocular_acuity_difference": interocular_acuity_difference(p),
        "selection_rationale": (
            "Lowest constrained multi-objective loss while maintaining required "
            "visual acuity, binocular compatibility, adaptation tolerance, and "
            "manufacturing feasibility."
        ),
        "explainable_summary": _explainable_summary(
            p, phenotype, indices, pair_label, overruled=overruled,
            out_of_range=out_of_range,
        ),
        "prediction_confidence": confidence,
        "confidence_breakdown": {
            "measurement_coverage": round(coverage * 100, 1),
            "measurement_plausibility": round(plausible * 100, 1),
        },
        # Named explicitly so a clinician sees WHICH value pulled confidence
        # down, rather than only that it fell.
        "out_of_range_measurements": out_of_range,
        "measured_domains": measured,
        "credited_domains": credited,
        "accommodative_demand_d": accommodative_demand_d(p),
        "recommended_follow_up": (
            "6 months" if confidence >= cfg.confidence_for_long_followup else "3 months"
        ),
        "manufacturing_status": "Ready",
        "primary_goal": p.primary_goal,
        # Which predictor and parameter set produced this. Essential once a
        # learned model is in play: a stored result must name what made it.
        "engine": od_metrics["provenance"],
    }

    design = {
        "design_id": design_id,
        "recipes": [od_recipe, os_recipe],
        "revision": 1,
        "site": site or cfg.default_site,
        "candidates": per_eye,
    }
    REGISTRY.register(design_id, design)
    return {"clinical": clinical, "design": design}


def clinical_only(p: PatientInput) -> Dict[str, Any]:
    """Run a fitting and return the screened clinical payload only."""
    result = run_fitting(p)["clinical"]
    assert_no_design_leak(result)
    return result


# --------------------------------------------------------------------------- #
# Follow-up.
#
# ``nso_core.run_followup`` names the internal profile tier, which is a design
# term. The clinical layer reports the direction of change instead, so the
# follow-up screen can drive a refit without naming the design ladder.
# --------------------------------------------------------------------------- #

SUPPORT_LEVELS = {"Low": "Level 1", "Medium": "Level 2", "High": "Level 3"}

CLINICAL_ADVICE = {
    "Maintain current NSO profile": "Maintain the current design",
    "Consider increasing one level": "Consider increasing optical support one level",
    "Escalate NSO strength and schedule clinical review":
        "Increase to maximum optical support and schedule a clinical review",
}


def clinical_followup(
    baseline_al: float,
    followup_al: float,
    interval_months: int,
    current_support_level: str = "Level 2",
) -> Dict[str, Any]:
    """Progression assessment expressed in clinical, not design, terms."""
    cfg = get_config()
    tier = {v: k for k, v in SUPPORT_LEVELS.items()}.get(current_support_level, "Medium")
    raw = engine.run_followup(baseline_al, followup_al, interval_months, tier)
    next_level = SUPPORT_LEVELS[raw["next_profile"]]

    annualized = raw["annualized_delta_al"]
    out = {
        "delta_al": raw["delta_al"],
        "annualized_delta_al": annualized,
        "advice": CLINICAL_ADVICE[raw["advice"]],
        "current_support_level": current_support_level,
        "next_support_level": next_level,
        "refit_required": next_level != current_support_level,
        "progression_band": (
            "Controlled"
            if annualized <= cfg.progression_controlled_ceiling
            else "Borderline"
            if annualized <= cfg.progression_borderline_ceiling
            else "Progressing"
        ),
    }
    assert_no_design_leak(out)
    return out


# --------------------------------------------------------------------------- #
# Closed loop: clinical feedback -> AI refitting.
#
# ASSUMPTIONS P1-6: how far a given progression rate should shift the design is
# a calibration question. The adjustment is monotone but uncalibrated.
# --------------------------------------------------------------------------- #

def refit(
    p: PatientInput,
    previous_design_id: str,
    baseline_al: float,
    followup_al: float,
    interval_months: int,
    site: str | None = None,
) -> Dict[str, Any]:
    """Re-fit from observed progression. Returns the clinical layer only."""
    if not REGISTRY.known(previous_design_id):
        raise KeyError(previous_design_id)

    fu = clinical_followup(baseline_al, followup_al, interval_months)
    escalation = get_config().refit_escalation[fu["progression_band"]]

    # Fold the observed progression back in: the measured axial length replaces
    # the stale reading, and persistent progression is treated as additional
    # refractive load rather than as a new prescription.
    updated = replace(
        p,
        od=replace(p.od, axial_length=max(p.od.axial_length, followup_al)),
        os=replace(p.os, axial_length=max(p.os.axial_length, followup_al)),
        progression_load=escalation,
    )

    clinical = run_fitting(updated, site=site)["clinical"]
    revision = REGISTRY.link_revision(clinical["design_id"], previous_design_id)

    clinical["refit"] = {
        "previous_design_id": previous_design_id,
        "progression_band": fu["progression_band"],
        "annualized_delta_al": fu["annualized_delta_al"],
        "escalation_applied": escalation > 0,
        "design_changed": clinical["design_id"] != previous_design_id,
        "revision": revision,
    }
    assert_no_design_leak(clinical)
    return clinical
