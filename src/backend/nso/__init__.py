"""
NSO AI-PC Fitting engine (V2).

Layers, outermost first. Each imports only from the ones below it, so the IP
boundary is enforced by the dependency graph and not only by the guard:

    clinical      what a clinician sees; the only layer that crosses the network
    design        phenotype -> recipe -> candidates -> chosen OD/OS pair
    manufacturing geometry projection, segmented release, verification
    predictors    outcome forecasting -- the swap point for a trained model
    features      feature extraction; the ML seam, with a versioned schema
    phenotype     R/B/S/N/T grading
    patient       clinical input
    recipe        the design recipe (Design IP)
    config        every tunable constant
    ip            the leak guard

Where to make a change:

    a coefficient or threshold        -> config.py (never a literal in engine code)
    a new clinical input              -> patient.py, then features.py
    a new model                       -> predictors/, register it, activate it
    how a design is synthesized       -> design/synthesis.py
    what a vendor receives            -> manufacturing/geometry.py
    what the clinician sees           -> clinical.py

ASSUMPTIONS.md catalogues every value that is a placeholder rather than data.
"""

from .clinical import (
    CLINICAL_ADVICE,
    SUPPORT_LEVELS,
    clinical_followup,
    clinical_only,
    refit,
    run_fitting,
)
from .config import CONFIG, EngineConfig, get_config, use_config
from .design import (
    binocular_penalty,
    candidate_loss,
    design_for_eye,
    design_id_for,
    generate_candidates,
    optimize_pair,
    pair_class,
)
from .features import (
    FeatureVector,
    accommodative_demand_d,
    ai_derived_indices,
    csf_descriptors,
    design_features,
    interocular_acuity_difference,
    out_of_range_measurements,
    patient_features,
    plausibility,
    plausibly_measured_domains,
    corneal_asphericity_departure,
    corneal_sa_departure,
    dominance_bias,
    residual_aberration_load,
    residual_astigmatism,
    sheard_deficit,
    vergence_direction,
    training_row,
)
from .ip import DesignLeakError, assert_no_design_leak
from .manufacturing import (
    REGISTRY,
    SEGMENT_CODES,
    VENDOR_SEGMENTS,
    DesignRegistry,
    DesignStore,
    InMemoryDesignStore,
    PostgresDesignStore,
    SQLiteDesignStore,
    default_store,
    store_from_url,
    microstructure_map,
    geometric_verification,
    surface_map,
)
from .patient import PRIMARY_GOALS, EyeInput, PatientInput
from .phenotype import PHENOTYPE_DOMAINS, grade, visual_phenotype
from .predictors import (
    OutcomePrediction,
    Predictor,
    RuleBasedPredictor,
    SchemaMismatchError,
    get_predictor,
    register,
    use_predictor,
)
from .recipe import DesignRecipe

__version__ = "2.1.0"

__all__ = [
    "EyeInput", "PatientInput", "DesignRecipe", "PRIMARY_GOALS",
    "CONFIG", "EngineConfig", "get_config", "use_config",
    "FeatureVector", "patient_features", "design_features", "training_row",
    "ai_derived_indices", "csf_descriptors", "interocular_acuity_difference",
    "accommodative_demand_d", "out_of_range_measurements", "plausibility",
    "plausibly_measured_domains", "vergence_direction", "sheard_deficit", "corneal_sa_departure",
    "corneal_asphericity_departure", "residual_astigmatism",
    "residual_aberration_load", "dominance_bias",
    "PHENOTYPE_DOMAINS", "visual_phenotype", "grade",
    "Predictor", "OutcomePrediction", "RuleBasedPredictor",
    "SchemaMismatchError", "get_predictor", "register", "use_predictor",
    "design_for_eye", "generate_candidates", "candidate_loss",
    "optimize_pair", "binocular_penalty", "pair_class", "design_id_for",
    "surface_map", "microstructure_map", "geometric_verification",
    "REGISTRY", "DesignRegistry", "DesignStore", "InMemoryDesignStore",
    "SQLiteDesignStore", "PostgresDesignStore", "default_store", "store_from_url",
    "SEGMENT_CODES", "VENDOR_SEGMENTS",
    "run_fitting", "clinical_only", "clinical_followup", "refit",
    "SUPPORT_LEVELS", "CLINICAL_ADVICE",
    "assert_no_design_leak", "DesignLeakError",
]
