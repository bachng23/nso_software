"""
Candidate generation and the constrained multi-objective loss.

The loss is deliberately kept out of the predictor: a predictor says what will
happen, the loss says what we value. Separating them means a new model changes
the forecast without silently changing clinical priorities, and the weights
stay reviewable in ``EngineConfig``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..config import get_config
from ..features import design_features, patient_features
from ..patient import PatientInput
from ..predictors import get_predictor
from ..recipe import DesignRecipe
from .synthesis import design_for_eye


def candidate_metrics(
    recipe: DesignRecipe, p: PatientInput, indices: Dict[str, Any]
) -> Dict[str, Any]:
    """Predicted outcomes for one candidate, before any weighting."""
    cfg = get_config()
    prediction = get_predictor().predict(patient_features(p), design_features(recipe))
    m = prediction.as_metrics()

    # Robustness combines the patient's tolerance with this design's optical
    # load, so the term actually distinguishes candidates.
    m["robustness"] = round(
        indices["dynamic_robustness"]
        * (1.0 - cfg.robustness_entropy_penalty * recipe.entropy / 100.0),
        1,
    )
    m["feasible"] = (
        m["acuity"] >= cfg.min_acuity
        and m["adaptation"] >= cfg.min_adaptation
        and m["manufacturability"] >= cfg.min_manufacturability
        and cfg.sa_min_d <= recipe.nso_peak_target_d <= cfg.sa_max_d
    )
    m["provenance"] = prediction.provenance()
    return m


# Objectives the loss trades off. All are "higher is better".
OBJECTIVES = ("control", "acuity", "comfort", "adaptation", "robustness",
              "manufacturability")


def _normalized(values: List[float]) -> List[float]:
    """Min-max scale a set of scores to 0..1 within the candidate set.

    Weights are only meaningful if the terms they weight are on one scale.
    Raw scores are not: across a candidate set, control moves ~18 points while
    acuity moves ~4, so an unnormalized loss let control dominate at any
    weighting and the primary goal barely mattered. Scaling by the observed
    spread makes a 0.35 weight mean 35% of the decision.

    A term with no spread contributes nothing -- it cannot distinguish
    candidates, so it should not tilt the choice.
    """
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return [0.0] * len(values)
    return [(v - low) / (high - low) for v in values]


def score_candidates(
    candidates: List[Dict[str, Any]], goal: str
) -> List[Dict[str, Any]]:
    """Attach the weighted, scale-normalized loss to each candidate."""
    cfg = get_config()
    weights = cfg.weights_for_goal(goal)

    scaled = {
        objective: _normalized([c["metrics"][objective] for c in candidates])
        for objective in OBJECTIVES
    }

    for i, candidate in enumerate(candidates):
        metrics = candidate["metrics"]
        # Augmented weighted Tchebycheff, not a weighted sum.
        #
        # A weighted SUM over a discrete candidate set always lands on an
        # extreme point -- that is a property of maximizing a linear function,
        # not a quirk of these numbers. It is why nine clinical goals could only
        # ever produce two designs: the gentlest and the strongest. It also let
        # the five "tolerability" objectives, which all move together, outvote
        # control five-to-one whatever the weights said.
        #
        # Tchebycheff minimizes the WORST weighted shortfall instead, so a
        # design that is merely good everywhere can beat one that is perfect on
        # five objectives and hopeless on the sixth. That is what makes an
        # intermediate design reachable, and it is the standard remedy rather
        # than a tuning trick.
        gaps = [
            weights[objective] * (1.0 - scaled[objective][i])
            for objective in OBJECTIVES
        ]
        loss = max(gaps) + cfg.scalarization_augmentation * sum(gaps)
        # Infeasible designs are pushed out of contention rather than silently
        # selected. The penalty exceeds the whole normalized range, so no
        # weighting can bring one back.
        if not metrics["feasible"]:
            loss += 1.0
        metrics["loss"] = round(loss, 4)
    return candidates


def candidate_loss(
    recipe: DesignRecipe, p: PatientInput, indices: Dict[str, Any]
) -> Dict[str, Any]:
    """Metrics for a single candidate, scored on its own.

    Retained for callers that score one design in isolation. Note the loss is
    then degenerate -- normalization needs a set to normalize against -- so
    ranking must go through :func:`generate_candidates`.
    """
    metrics = candidate_metrics(recipe, p, indices)
    return score_candidates([{"metrics": metrics}], p.primary_goal)[0]["metrics"]


def generate_candidates(
    p: PatientInput, eye_key: str, indices: Dict[str, float]
) -> List[Dict[str, Any]]:
    """The candidate grid for one eye: SA offset x density offset.

    Two axes rather than one. On a single SA axis every candidate traded the
    same way, so whichever end the loss weights favoured won and the primary
    goal could only produce two distinct answers. Density moves entropy --
    and through it robustness and adaptation -- independently of SA.

    Labels stay stable for a given grid: A, B, C, ... in grid order, assigned
    before ranking, so the same label means the same design across requests.
    """
    cfg = get_config()
    out = []
    index = 0
    for sa_bias in cfg.candidate_offsets:
        for density_bias in cfg.candidate_density_offsets:
            recipe = design_for_eye(
                p, eye_key, indices, sa_bias=sa_bias, density_bias=density_bias
            )
            out.append(
                {
                    "label": chr(ord("A") + index),
                    "recipe": recipe,
                    "metrics": candidate_metrics(recipe, p, indices),
                }
            )
            index += 1

    score_candidates(out, p.primary_goal)
    return sorted(out, key=lambda c: c["metrics"]["loss"])
