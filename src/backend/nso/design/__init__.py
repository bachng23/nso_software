"""Design IP layer: phenotype -> recipe -> candidates -> chosen pair."""

from .candidates import candidate_loss, generate_candidates
from .identity import design_id_for, revision_id_for
from .joint import binocular_penalty, binocular_weight, optimize_pair, pair_class
from .synthesis import design_for_eye

__all__ = [
    "candidate_loss", "generate_candidates", "design_id_for", "revision_id_for",
    "binocular_penalty", "binocular_weight", "optimize_pair", "pair_class",
    "design_for_eye",
]
