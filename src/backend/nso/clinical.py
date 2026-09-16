"""
The clinical layer — the only thing that crosses the network boundary.

Assembles the payload a clinician sees: Design ID, phenotype, indices,
predicted outcomes, and why this design was chosen. Every value here is an
outcome or an explanation; none of it is a design parameter.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import Any, Dict, List

import nso_core as engine

from .config import get_config
from .design import design_id_for, optimize_pair, pair_class
from .design.identity import revision_id_for
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
from .predictors import get_predictor


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
            "Age and axial-length findings increase the measured refractive-risk score."
        )
    if indices["visual_stress"] >= 60:
        lines.append(
            "Elevated reported visual stress lowers the expected comfort and adaptation scores."
        )
    if indices["binocular_load"] >= 55:
        lines.append(
            "Near phoria and convergence findings increase measured binocular demand."
        )
    if indices["accommodative_stress"] >= 55:
        lines.append(
            "Accommodative lag and sustained near work increase accommodative demand."
        )
    if indices["spatial_frequency_sensitivity"] < 55:
        lines.append(
            "Reduced contrast sensitivity lowers the contrast-optimization fit score."
        )
    if pair_label != "Symmetric":
        lines.append("Interocular findings were considered when balancing the recommendation.")
    if overruled:
        lines.append("The recommendation accounts for binocular balance rather than either eye alone.")
    if out_of_range:
        names = ", ".join(f["measurement"].replace("_", " ") for f in out_of_range)
        lines.append(
            f"Confidence reduced: {names} fall outside the range these rules were "
            "written for, so the prediction is an extrapolation."
        )
    lines.append(f"Primary optimization goal: {p.primary_goal}.")
    return lines


def _stable_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()[:20].upper()
    return f"{prefix}-{digest}"


def run_fitting(
    p: PatientInput,
    site: str | None = None,
    *,
    patient_id: str | None = None,
    clinical_dataset_id: str | None = None,
    design_id_override: str | None = None,
    revision: int = 0,
    previous_design_id: str | None = None,
    reason: str = "initial fitting",
    outcome_reference: str | None = None,
) -> Dict[str, Any]:
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
    patient_snapshot = asdict(p)
    patient_id = patient_id or _stable_id("PAT", patient_snapshot)
    clinical_dataset_id = clinical_dataset_id or _stable_id(
        "CDS", {"patient_id": patient_id, "input": patient_snapshot}
    )
    root_design_id = design_id_for(
        [od_recipe, os_recipe], context={
            "patient_id": patient_id,
            "dataset": clinical_dataset_id,
            "config_version": cfg.version,
            "predictor": get_predictor().name,
            "predictor_version": get_predictor().version,
        }
    )
    design_id = design_id_override or revision_id_for(root_design_id, revision)

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
        "patient_id": patient_id,
        "clinical_dataset_id": clinical_dataset_id,
        "phenotype": phenotype,
        "indices": indices,
        "eyes": {
            "OD": {"profile_label": "Personalized Functional Profile A"},
            "OS": {"profile_label": "Personalized Functional Profile B"},
        },
        "binocular_pair": pair_label,
        # NOT a validated efficacy probability. The supervisor's V2.1 baseline
        # is explicit: until prototype MTF/PSF/CSF data and an axial-length
        # clinical dataset exist, this is a relative score, and calling it a
        # percentage of myopia control claims something the model cannot
        # support. The name says what it is.
        "predicted": {
            "nso_control_score": _pct(mean("control")),
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
        # The protocol travels with the numbers. Without it a clinician cannot
        # tell an AUC computed at the instrument's own frequencies from one
        # computed against an assumption the engine made.
        "csf_protocol": (
            {
                "measured": True,
                "device": p.csf().device,
                "test_protocol": p.csf().test_protocol,
                "spatial_frequency_cpd": p.csf().spatial_frequency_cpd,
                "frequencies_assumed": p.csf().frequencies_assumed,
            }
            if p.csf_is_measured
            else {"measured": False, "source": f"band: {p.csf_band}"}
        ),
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
        # "Recorded" and "used" are different things, and a clinician who ran a
        # VEP deserves to see which one happened.
        "neurovisual_status": (
            "interpretable" if p.has_interpretable_neurovisual
            else "recorded_not_interpretable" if p.neurovisual_recorded
            else "not_recorded"
        ),
        "accommodative_demand_d": accommodative_demand_d(p),
        "recommended_follow_up": (
            "6 months" if confidence >= cfg.confidence_for_long_followup else "3 months"
        ),
        "manufacturing_status": "Ready",
        "primary_goal": p.primary_goal,
        # Which predictor and parameter set produced this. Essential once a
        # learned model is in play: a stored result must name what made it.
        "engine": od_metrics["provenance"],
        "clinical_recommendation": (
            "Review inputs before approval" if out_of_range
            else "Recommendation ready for clinician approval"
        ),
    }

    # The two optical channels are stored as independent objects, as the V2.1
    # baseline requires. Keeping them separate in the database is what stops a
    # later reader from treating microstructure modulation as base-surface sag
    # -- the conflation this release exists to undo.
    design = {
        "base_surface_optical_profile": {
            eye: asdict(recipe.base_surface)
            for eye, recipe in (("OD", od_recipe), ("OS", os_recipe))
        },
        "nso_spatial_modulation_profile": {
            eye: asdict(recipe.nso_modulation)
            for eye, recipe in (("OD", od_recipe), ("OS", os_recipe))
        },
        "design_id": design_id,
        "recipes": [od_recipe, os_recipe],
        "revision": revision,
        "root_design_id": design_id.split("-R", 1)[0],
        "previous_design_id": previous_design_id,
        "patient_id": patient_id,
        "clinical_dataset_id": clinical_dataset_id,
        "algorithm_version": od_metrics["provenance"].get("predictor_version", "unknown"),
        "design_engine_version": "2.1",
        "manufacturing_version": "2.1",
        # A clinical-only snapshot used later to compare an observed follow-up
        # with the original compatibility signal.  It deliberately stores no
        # optical or manufacturing parameters.
        "clinical_expectation": {
            "myopia_management_fit": clinical["predicted"]["nso_control_score"],
            "prediction_confidence": clinical["prediction_confidence"],
        },
        "reason": reason,
        "outcome_reference": outcome_reference,
        "status": "Proposed",
        "site": site or cfg.default_site,
        "candidates": per_eye,
    }
    patient_created = REGISTRY._record_domain(
        "patient", patient_id, {"patient_id": patient_id}
    )
    dataset_created = REGISTRY._record_domain("clinical_dataset", clinical_dataset_id, {
        "clinical_dataset_id": clinical_dataset_id,
        "patient_id": patient_id,
        "input": patient_snapshot,
    })
    if not patient_created and dataset_created:
        REGISTRY._audit(
            "patient_profile_modified", patient_id, object_type="patient"
        )
    REGISTRY.register(design_id, design)
    return {"clinical": clinical, "design": design}


def clinical_only(p: PatientInput) -> Dict[str, Any]:
    """Run a fitting and return the screened clinical payload only."""
    result = run_fitting(p)["clinical"]
    assert_no_design_leak(result)
    return result


# Explicit public response contract. Adding a new internal field can never
# accidentally make it cross the clinical API boundary.
CLINICAL_RESPONSE_FIELDS = frozenset({
    "design_id", "patient_id", "clinical_dataset_id", "phenotype", "indices",
    "eyes", "binocular_pair", "predicted", "spatial_frequency_descriptors",
    "csf_protocol", "interocular_acuity_difference", "explainable_summary",
    "prediction_confidence", "out_of_range_measurements", "neurovisual_status",
    "accommodative_demand_d", "recommended_follow_up", "manufacturing_status",
    "primary_goal", "clinical_recommendation", "refit",
})


def public_clinical(payload: Dict[str, Any]) -> Dict[str, Any]:
    public = {key: payload[key] for key in CLINICAL_RESPONSE_FIELDS if key in payload}
    if "predicted" in public:
        scores = public["predicted"]
        public["predicted"] = {
            "myopia_management_fit": scores["nso_control_score"],
            "visual_comfort": scores["visual_comfort"],
            "adaptation": scores["adaptation"],
            "binocular_compatibility": scores["binocular_compatibility"],
        }
    assert_no_design_leak(public)
    return public


def public_clinical_only(
    p: PatientInput, *, patient_id: str | None = None
) -> Dict[str, Any]:
    clinical = run_fitting(p, patient_id=patient_id)["clinical"]
    REGISTRY._audit("clinical_response_generated", clinical["design_id"])
    return public_clinical(clinical)


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
    *,
    baseline_od_al: float | None = None,
    followup_od_al: float | None = None,
    baseline_os_al: float | None = None,
    followup_os_al: float | None = None,
    baseline_comfort: float | None = None,
    current_comfort: float | None = None,
    visual_stress_score: float | None = None,
    average_wear_hours: float | None = None,
    compliance: str | None = None,
    original_fit_score: float | None = None,
    original_prediction_confidence: float | None = None,
) -> Dict[str, Any]:
    """Progression assessment expressed in clinical, not design, terms."""
    cfg = get_config()
    tier = {v: k for k, v in SUPPORT_LEVELS.items()}.get(current_support_level, "Medium")
    od_base = baseline_od_al if baseline_od_al is not None else baseline_al
    od_follow = followup_od_al if followup_od_al is not None else followup_al
    os_base = baseline_os_al if baseline_os_al is not None else baseline_al
    os_follow = followup_os_al if followup_os_al is not None else followup_al
    od_raw = engine.run_followup(od_base, od_follow, interval_months, tier)
    os_raw = engine.run_followup(os_base, os_follow, interval_months, tier)
    raw = od_raw if od_raw["annualized_delta_al"] >= os_raw["annualized_delta_al"] else os_raw
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
        "eyes": {
            "OD": {"delta_al": od_raw["delta_al"], "annualized_delta_al": od_raw["annualized_delta_al"]},
            "OS": {"delta_al": os_raw["delta_al"], "annualized_delta_al": os_raw["annualized_delta_al"]},
        },
        "comfort_change": (
            round(current_comfort - baseline_comfort, 2)
            if baseline_comfort is not None and current_comfort is not None else None
        ),
    }
    needs_review = (
        compliance in {"Poor", "Unknown"}
        or (visual_stress_score is not None and visual_stress_score >= 8)
        or (average_wear_hours is not None and average_wear_hours < 4)
    )
    out["action_class"] = (
        "ADJUST" if needs_review
        else "RE-FIT" if out["refit_required"]
        else "CONTINUE"
    )
    out["responder_status"] = (
        "Responder" if out["progression_band"] == "Controlled"
        else "Borderline responder" if out["progression_band"] == "Borderline"
        else "Suboptimal responder"
    )
    if original_fit_score is None:
        out["deviation_from_original_prediction"] = None
    else:
        fit_category = (
            "Excellent" if original_fit_score >= 80
            else "Good" if original_fit_score >= 65
            else "Fair" if original_fit_score >= 50
            else "Low"
        )
        expected_rank = (
            2 if fit_category in {"Excellent", "Good"}
            else 1 if fit_category == "Fair"
            else 0
        )
        observed_rank = {
            "Controlled": 2, "Borderline": 1, "Progressing": 0,
        }[out["progression_band"]]
        category_steps = observed_rank - expected_rank
        direction = (
            "More favorable" if category_steps > 0
            else "Less favorable" if category_steps < 0
            else "Aligned"
        )
        comparison = (
            f"Aligned with the original {fit_category.lower()} "
            "design–phenotype compatibility signal."
            if direction == "Aligned"
            else f"{direction} than the original {fit_category.lower()} "
            "design–phenotype compatibility signal."
        )
        out["deviation_from_original_prediction"] = {
            "original_fit_score": round(float(original_fit_score), 1),
            "original_fit_category": fit_category,
            "prediction_confidence": (
                round(float(original_prediction_confidence), 1)
                if original_prediction_confidence is not None else None
            ),
            "observed_responder_classification": out["responder_status"],
            "category_steps": category_steps,
            "direction": direction,
            "summary": comparison,
        }
    out["exposure"] = {
        "average_wear_hours": average_wear_hours,
        "compliance": compliance,
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

    previous = REGISTRY._get(previous_design_id)
    expectation = previous.get("clinical_expectation", {})
    fu = clinical_followup(
        baseline_al,
        followup_al,
        interval_months,
        original_fit_score=expectation.get("myopia_management_fit"),
        original_prediction_confidence=expectation.get("prediction_confidence"),
    )
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

    revision = int(previous.get("revision", 0)) + 1
    root_id = previous.get("root_design_id") or previous_design_id.split("-R", 1)[0]
    new_design_id = revision_id_for(root_id, revision)
    while REGISTRY.known(new_design_id):
        revision += 1
        new_design_id = revision_id_for(root_id, revision)
    outcome_id = _stable_id("OUT", {
        "design_id": previous_design_id,
        "interval_months": interval_months,
        "baseline_al": baseline_al,
        "followup_al": followup_al,
    })
    REGISTRY._record_outcome(outcome_id, previous_design_id, {
        "outcome_id": outcome_id,
        "design_id": previous_design_id,
        "derived": fu,
    })
    clinical = run_fitting(
        updated,
        site=site,
        patient_id=previous.get("patient_id"),
        design_id_override=new_design_id,
        revision=revision,
        previous_design_id=previous_design_id,
        reason="follow-up optimization",
        outcome_reference=outcome_id,
    )["clinical"]
    REGISTRY._audit("design_reoptimized", new_design_id, revision)
    REGISTRY._audit("design_revision_created", new_design_id, revision)

    clinical["refit"] = {
        "previous_design_id": previous_design_id,
        "progression_band": fu["progression_band"],
        "annualized_delta_al": fu["annualized_delta_al"],
        "escalation_applied": escalation > 0,
        "design_changed": clinical["design_id"] != previous_design_id,
        "revision": revision,
        "outcome_reference": outcome_id,
    }
    assert_no_design_leak(clinical)
    return clinical
