"""
Joint (binocular) optimization.

Optimizing each eye on its own and reporting the pair afterwards is not what
the V2 spec asks for: two individually optimal monocular designs can fuse
badly. The pair is chosen over the CROSS PRODUCT of the per-eye candidates,
with a binocular penalty inside the objective.

ASSUMPTIONS.md P0-6: the fusion tolerances are engineering placeholders. Real
aniseikonia limits must come from clinical data.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from nso_core import norm

from ..config import get_config
from ..patient import PatientInput
from ..recipe import DesignRecipe
from .candidates import generate_candidates


def binocular_penalty(
    od: DesignRecipe, os_: DesignRecipe, indices: Dict[str, float]
) -> float:
    """0-1 cost of fusing these two designs. 0 = perfectly compatible."""
    cfg = get_config()
    sa_excess = max(0.0, abs(od.sa_strength - os_.sa_strength) - cfg.sa_fusion_tolerance_d)
    dens_excess = max(
        0.0,
        abs(od.spatial_density_per_mm2 - os_.spatial_density_per_mm2)
        - cfg.density_fusion_tolerance,
    )
    mtf_diff = abs(od.target_mtf_modulation - os_.target_mtf_modulation)

    raw = (
        0.45 * norm(sa_excess, 0.0, 3.0)
        + 0.25 * norm(dens_excess, 0.0, 120.0)
        + 0.30 * norm(mtf_diff, 0.0, 0.25)
    )
    # An eye that already fuses poorly tolerates less optical disparity.
    fragility = 1.0 + 0.5 * (1 - indices["interocular_image_balance"] / 100.0)
    return float(min(raw * fragility, 1.0))


def pair_class(od: DesignRecipe, os_: DesignRecipe) -> str:
    """Clinical label for the pair. Shares the optimizer's tolerances so the
    label and the penalty can never disagree (ASSUMPTIONS P2-4)."""
    cfg = get_config()
    delta_sa = abs(od.sa_strength - os_.sa_strength)
    delta_density = abs(od.spatial_density_per_mm2 - os_.spatial_density_per_mm2)
    if delta_sa < cfg.sa_fusion_tolerance_d and delta_density < cfg.density_fusion_tolerance:
        return "Symmetric"
    if delta_sa < cfg.mild_sa_ceiling:
        return "Mildly Asymmetric"
    return "Asymmetric"


def optimize_pair(
    p: PatientInput, indices: Dict[str, float]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Choose the OD/OS pair minimizing mean monocular loss + binocular cost."""
    cfg = get_config()
    od_candidates = generate_candidates(p, "OD", indices)
    os_candidates = generate_candidates(p, "OS", indices)

    scored = []
    for od in od_candidates:
        for os_ in os_candidates:
            penalty = binocular_penalty(od["recipe"], os_["recipe"], indices)
            mono = (od["metrics"]["loss"] + os_["metrics"]["loss"]) / 2.0
            joint = mono + cfg.binocular_weight * penalty
            feasible = od["metrics"]["feasible"] and os_["metrics"]["feasible"]
            scored.append(
                {
                    "od": od,
                    "os": os_,
                    "binocular_penalty": round(penalty, 4),
                    "monocular_loss": round(mono, 4),
                    "joint_loss": round(joint + (0.0 if feasible else 1.0), 4),
                    "feasible": feasible,
                }
            )

    scored.sort(key=lambda x: x["joint_loss"])
    return scored, od_candidates, os_candidates
