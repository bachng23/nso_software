"""Visual phenotype R / B / S / N / T — clinical-layer output."""

from __future__ import annotations

from typing import Any, Dict

from .config import get_config
from .features import task_load_index
from .patient import PatientInput

PHENOTYPE_DOMAINS = ("R", "B", "S", "N", "T")

DOMAIN_LABELS = {
    "R": "Refractive State",
    "B": "Binocular State",
    "S": "Spatial Visual Response",
    "N": "Neurovisual Response",
    "T": "Task & Environmental State",
}

GRADE_WORDS = {1: "Low", 2: "Moderate", 3: "High"}


def grade(score: float) -> int:
    """1..3, where 3 always means 'this domain drives the design hardest'."""
    cfg = get_config()
    if score < cfg.grade_low_ceiling:
        return 1
    if score < cfg.grade_mid_ceiling:
        return 2
    return 3


def visual_phenotype(p: PatientInput, indices: Dict[str, float]) -> Dict[str, Any]:
    """Five-domain phenotype. Safe to expose."""
    domain_scores = {
        "R": indices["refractive_risk"],
        "B": indices["binocular_load"],
        # Spatial: invert the quality index so high = more demanding.
        "S": round(100.0 - indices["spatial_frequency_sensitivity"], 1),
        "N": indices["visual_stress"],
        "T": task_load_index(p),
    }
    grades = {d: grade(s) for d, s in domain_scores.items()}
    return {
        "code": "-".join(f"{d}{grades[d]}" for d in PHENOTYPE_DOMAINS),
        "domains": [
            {
                "key": d,
                "name": DOMAIN_LABELS[d],
                "score": domain_scores[d],
                "grade": grades[d],
                "grade_label": GRADE_WORDS[grades[d]],
            }
            for d in PHENOTYPE_DOMAINS
        ],
        "dominant_domain": DOMAIN_LABELS[max(domain_scores, key=domain_scores.get)],
    }
