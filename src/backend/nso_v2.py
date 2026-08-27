"""
DEPRECATED — compatibility shim for the pre-package layout.

The engine now lives in the ``nso`` package (see ``nso/__init__.py`` for the
layer map). This module re-exports the same names so existing imports keep
working; new code should import from ``nso`` directly.

It exists mainly to prove the split was behaviour-preserving: the original test
suite runs against this shim unchanged.
"""

from nso import *                                     # noqa: F401,F403
from nso import (                                     # noqa: F401
    CONFIG,
    PHENOTYPE_DOMAINS,
    REGISTRY,
    SEGMENT_CODES,
    SUPPORT_LEVELS,
    binocular_penalty,
    candidate_loss,
    design_for_eye,
    generate_candidates,
    grade,
    optimize_pair,
    pair_class,
)
from nso.clinical import CLINICAL_ADVICE               # noqa: F401
from nso.features import (                             # noqa: F401
    accommodative_stress_index,
    binocular_load_index,
    dynamic_robustness_index,
    eye_risk,
    interocular_image_balance_index,
    neural_adaptation_index,
    refractive_risk_index,
    spatial_frequency_sensitivity_index,
    task_load_index,
    visual_stress_index,
)
from nso.ip import FORBIDDEN_KEY_FRAGMENTS             # noqa: F401
from nso.manufacturing.geometry import (               # noqa: F401
    microstructure_element_count,
    microstructure_page_count,
)

# -- Names the old module exposed with a leading underscore ----------------- #
_grade = grade
_binocular_penalty = binocular_penalty
_design_for_eye = design_for_eye
_candidate_loss = candidate_loss
_generate_candidates = generate_candidates
_optimize_pair = optimize_pair
_binocular_pair_class = pair_class
_eye_risk = eye_risk
_clip100 = __import__("nso.features", fromlist=["clip100"]).clip100

# -- Constants the old module exposed at module level ------------------------ #
# Snapshots of the active config, kept for backwards compatibility. Engine code
# reads ``CONFIG`` live; these do not follow a config swap.
CSF_BANDS = CONFIG.csf_bands
# Retired: frequencies now travel with each measurement rather than being a
# global constant. Kept as the assumed-frequency fallback for old callers.
from nso.csf import ASSUMED_FREQUENCIES_CPD as CSF_FREQUENCIES_CPD  # noqa: E402
CANDIDATE_OFFSETS = CONFIG.candidate_offsets
BINOCULAR_WEIGHT = CONFIG.binocular_weight
SA_FUSION_TOLERANCE_D = CONFIG.sa_fusion_tolerance_d
# Renamed: the compared quantity is fill factor in percentage points, not
# areal density per mm^2. See nso/config.py.
DENSITY_FUSION_TOLERANCE = CONFIG.fill_fusion_tolerance_pct
FILL_FUSION_TOLERANCE_PCT = CONFIG.fill_fusion_tolerance_pct
LENS_INDEX = CONFIG.lens_index
OPTIC_ZONE_DIAMETER_MM = CONFIG.optic_zone_diameter_mm
BASE_CURVE_D = CONFIG.base_curve_d
SA_REFERENCE_SEMI_DIAMETER_MM = CONFIG.sa_reference_semi_diameter_mm
SURFACE_RADIAL_SAMPLES = CONFIG.surface_radial_samples
SURFACE_MERIDIONAL_SAMPLES = CONFIG.surface_meridional_samples
MICROSTRUCTURE_PAGE_SIZE = CONFIG.microstructure_page_size
SAG_TOLERANCE_MM = CONFIG.sag_tolerance_mm
HEIGHT_TOLERANCE_MM = CONFIG.height_tolerance_mm
POSITION_TOLERANCE_MM = CONFIG.position_tolerance_mm
DECENTRATION_TOLERANCE_MM = CONFIG.decentration_tolerance_mm
REFIT_ESCALATION = CONFIG.refit_escalation

# Renamed: it only ever checked geometry (see nso/manufacturing/verification.py).
optical_verification = geometric_verification
