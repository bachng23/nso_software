"""
FastAPI backend for the NSO AI-PC Fitting web UI (v2).

IP layering (per the "UI complexity != algorithm complexity" architecture note):

  * ``/api/predict`` accepts the six-section clinical profile and returns the
    CLINICAL LAYER only — Design ID, visual phenotype, AI-derived indices and
    predicted outcomes. The optical recipe (SA, microstructure geometry, fill
    factor, spatial density, jitter, temporal asymmetry) is computed
    server-side and never serialized into the response. Every response is
    screened by ``nso_v2.assert_no_design_leak`` before it is returned.

  * There is deliberately NO endpoint that returns a design recipe or a
    manufacturing CSV to a browser. Manufacturing is a submit-only flow
    (``/api/manufacturing/submit``); authorized vendors pull a single segment
    of the projection from ``/api/manufacturing/package`` with an API key.

Run (API only — the Next.js frontend calls it):
    python -m pip install -r requirements.txt
    python -m uvicorn api:app --reload --port 8000
API docs at http://127.0.0.1:8000/docs
"""

import hashlib
import json
import os
import time
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

import nso as v2
import report

_is_production = os.environ.get("NSO_ENV", "development").lower() == "production"
app = FastAPI(
    title="NSO AI-PC Fitting API",
    version="2.2",
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

# CORS: allow any *.vercel.app (production + preview deploys) and localhost.
# For a custom domain later, add it via ALLOWED_ORIGINS (comma-separated).
_prod_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
_origin_regex = r"https://([a-z0-9-]+\.)*vercel\.app|http://(localhost|127\.0\.0\.1)(:\d+)?"
app.add_middleware(
    CORSMiddleware,
    allow_origins=_prod_origins,
    allow_origin_regex=_origin_regex,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Per-vendor keys for the manufacturing segment API. One key per role, so a
# single leaked credential cannot assemble the whole design -- a shared key
# would defeat the point of segmenting the packages at all.
#
#   MANUFACTURING_KEY_FRONT_SURFACE=...   -> may pull FS
#   MANUFACTURING_KEY_BACK_SURFACE=...    -> may pull BS
#   MANUFACTURING_KEY_ASSEMBLY=...        -> may pull AV
#   MANUFACTURING_KEY_INTERNAL=...        -> internal QA; all segments
#
# A role with no key configured is disabled, not open.
def _vendor_keys() -> dict:
    keys = {}
    for role in v2.VENDOR_SEGMENTS:
        value = os.environ.get(f"MANUFACTURING_KEY_{role.upper()}", "")
        if value:
            keys[value] = role
    return keys


VENDOR_KEYS = _vendor_keys()


@app.middleware("http")
async def add_processing_time(request, call_next):
    """Expose coarse request timing without exposing internal pipeline details."""
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    response.headers["X-NSO-Processing-Ms"] = f"{elapsed_ms:.1f}"
    return response


def _authorize_vendor(api_key: str, segment: str) -> str:
    """Resolve a key to a vendor role and check it may pull this segment."""
    if not VENDOR_KEYS:
        raise HTTPException(status_code=503, detail="Manufacturing API not configured")
    role = VENDOR_KEYS.get(api_key)
    if role is None:
        raise HTTPException(status_code=401, detail="Invalid manufacturing API key")
    if segment not in v2.VENDOR_SEGMENTS[role]:
        raise HTTPException(
            status_code=403,
            detail=f"Vendor role '{role}' is not authorized for segment '{segment}'",
        )
    return role


@app.get("/api/health")
def health():
    predictor = v2.get_predictor()
    return {
        "status": "ok",
        "version": "2.2",
        "engine": predictor.name,
        "predictor_version": predictor.version,
        "feature_schema": v2.FeatureVector.SCHEMA_VERSION,
        "config_version": v2.get_config().version,
        "design_store": v2.REGISTRY.backend,
        "pipeline": [stage["stage"] for stage in v2.describe_pipeline()],
    }


# --------------------------------------------------------------------------- #
# Request models — the six sections of the V2 clinical profile.
#
# Tier 1 (Quick Fitting) fields are required; Tier 2 (Advanced Clinical Data)
# and Tier 3 (Research Mode) fields are all optional. NOTE: there is
# intentionally no way for a client to supply design parameters — the old
# ``sa_strength`` / ``density`` overrides were removed because they invert the
# IP boundary.
# --------------------------------------------------------------------------- #

class EyeIn(BaseModel):
    sphere: float = 0.0
    cylinder: float = 0.0
    axis: float = 0.0
    axial_length: float = 24.0
    bcva_logmar: Optional[float] = None
    orientation_id: Optional[str] = None
    nominal_axis_deg: Optional[float] = None
    settled_rotation_deg: Optional[float] = None
    rotation_sd_deg: Optional[float] = None
    recovery_time_s: Optional[float] = None
    asymmetry_index: Optional[float] = None
    temporal_nasal_ratio: Optional[float] = None


class PredictIn(BaseModel):
    patient_id: Optional[str] = None
    design_id: Optional[str] = None
    # -- Tier 1: Quick Fitting ---------------------------------------------
    age: float
    od: EyeIn
    os: EyeIn
    photopic_pupil: float = 4.5
    near_phoria: float = 0.0
    npc: float = 7.0
    accommodative_lag: float = 0.75
    csf_band: str = "Normal"
    visual_stress_score: float = 3.0
    near_hours: float = 6.0
    digital_hours: float = 4.0
    outdoor_hours: float = 1.5
    primary_goal: str = "Myopia Control"
    optimization_objective: Optional[str] = None

    # -- Tier 2: Advanced Clinical Data ------------------------------------
    mesopic_pupil: Optional[float] = None
    distance_phoria: Optional[float] = None
    pfv: Optional[float] = None
    nfv: Optional[float] = None
    ac_a: Optional[float] = None
    stereoacuity: Optional[float] = None
    ocular_dominance: str = "Balanced"
    binocular_balance: str = "Normal"
    fixation_disparity: Optional[float] = None
    symptom_questionnaire_score: Optional[float] = None
    amplitude_of_accommodation: Optional[float] = None
    accommodative_facility: Optional[float] = None
    nra: Optional[float] = None
    pra: Optional[float] = None
    bcc: Optional[float] = None
    mem: Optional[float] = None
    near_working_distance: Optional[float] = None
    computer_working_distance: Optional[float] = None
    visual_comfort_score: Optional[float] = None
    neural_adaptation_score: Optional[float] = None
    dynamic_visual_stability: Optional[float] = None
    typical_working_distance: Optional[float] = None
    night_driving: bool = False
    low_light_demand: str = "Moderate"

    # -- Tier 3: Research Mode ---------------------------------------------
    csf_low: Optional[float] = None
    csf_mid: Optional[float] = None
    csf_high: Optional[float] = None
    # Protocol, so the descriptors are computed at the frequencies the
    # instrument actually used rather than at ones the engine assumed.
    csf_frequencies_cpd: Optional[List[float]] = None
    csf_device: str = "unspecified"
    csf_test_protocol: str = "unspecified"
    csf_scale: str = "index_0_100"
    vep: Optional[float] = None
    vep_stimulus: str = "unspecified"
    vep_amplitude_uv: Optional[float] = None
    vep_latency_ms: Optional[float] = None
    vep_interocular_difference_ms: Optional[float] = None
    vep_z_score: Optional[float] = None
    erg: Optional[float] = None
    erg_protocol: str = "unspecified"
    erg_z_score: Optional[float] = None
    eye_tracking: Optional[float] = None
    fixation_stability: Optional[float] = None
    blink_rate: Optional[float] = None
    vergence_stability: Optional[float] = None
    pupil_dynamics: Optional[float] = None
    gaze_distribution: Optional[float] = None
    hoa_rms: Optional[float] = None
    corneal_sa: Optional[float] = None
    coma: Optional[float] = None
    trefoil: Optional[float] = None
    corneal_astigmatism: Optional[float] = None
    corneal_eccentricity: Optional[float] = None

    def to_patient(self) -> v2.PatientInput:
        # Subclasses (FollowupReportIn) carry extra non-clinical fields; keep
        # only what PatientInput actually declares.
        allowed = set(v2.PatientInput.__dataclass_fields__)
        data = {k: v for k, v in self.model_dump().items() if k in allowed}
        if self.optimization_objective:
            data["primary_goal"] = self.optimization_objective
        return v2.PatientInput(
            od=v2.EyeInput(**self.od.model_dump()),
            os=v2.EyeInput(**self.os.model_dump()),
            **{k: v for k, v in data.items() if k not in ("od", "os")},
        )


class FollowupIn(BaseModel):
    design_id: Optional[str] = None
    patient_id: Optional[str] = None
    baseline_al: Optional[float] = None
    followup_al: Optional[float] = None
    baseline_od_al: Optional[float] = None
    followup_od_al: Optional[float] = None
    baseline_os_al: Optional[float] = None
    followup_os_al: Optional[float] = None
    interval_months: Optional[float] = None
    baseline_date: Optional[str] = None
    followup_date: Optional[str] = None
    # Clinical support level, not the internal design tier.
    current_support_level: str = "Level 2"
    baseline_od_refraction: Optional[EyeIn] = None
    followup_od_refraction: Optional[EyeIn] = None
    baseline_os_refraction: Optional[EyeIn] = None
    followup_os_refraction: Optional[EyeIn] = None
    baseline_comfort: Optional[float] = None
    current_comfort: Optional[float] = None
    visual_stress_score: Optional[float] = None
    average_wear_hours: Optional[float] = None
    compliance: Optional[str] = None
    baseline_csf: Optional[float] = None
    current_csf: Optional[float] = None
    adverse_event: Optional[str] = None
    intolerance: Optional[str] = None


class FollowupReportIn(PredictIn):
    """Full patient inputs (for the prediction body) + the follow-up readings,
    so the follow-up report is the prediction report with a follow-up section."""
    baseline_al: Optional[float] = None
    followup_al: Optional[float] = None
    baseline_od_al: Optional[float] = None
    followup_od_al: Optional[float] = None
    baseline_os_al: Optional[float] = None
    followup_os_al: Optional[float] = None
    interval_months: Optional[float] = None
    baseline_date: Optional[str] = None
    followup_date: Optional[str] = None
    baseline_comfort: Optional[float] = None
    current_comfort: Optional[float] = None
    visual_stress_score_followup: Optional[float] = None
    average_wear_hours: Optional[float] = None
    compliance: Optional[str] = None
    baseline_csf: Optional[float] = None
    current_csf: Optional[float] = None
    adverse_event: Optional[str] = None
    intolerance: Optional[str] = None


class ManufacturingSubmitIn(BaseModel):
    design_id: str
    site: str = "SG"


class ManufacturingPackageIn(BaseModel):
    design_id: str
    segment: str   # opaque segment code: FS / BS / AV
    page: int = 0  # element-placement maps are paginated
    oem_id: str = "default"
    capability_profile_version: str = "1.0"


class ApprovalIn(BaseModel):
    design_id: str
    actor: str = "clinician"


class ClinicalResponse(BaseModel):
    """Browser-visible allowlist; unknown internal fields are rejected."""
    model_config = ConfigDict(extra="forbid")
    design_id: str
    design_version: str
    patient_id: str
    case_id: Optional[str] = None
    clinical_dataset_id: str
    prediction_id: str
    execution_id: str
    phenotype: Dict[str, Any]
    indices: Dict[str, Any]
    eyes: Dict[str, Any]
    binocular_pair: str
    predicted: Dict[str, Any]
    spatial_frequency_descriptors: Dict[str, Any]
    csf_protocol: Dict[str, Any]
    interocular_acuity_difference: Any
    explainable_summary: List[str]
    prediction_confidence: int
    prediction_confidence_category: str
    prediction_state: str
    predicted_response: Dict[str, Any]
    key_clinical_contributors: List[Dict[str, Any]]
    out_of_range_measurements: List[Dict[str, Any]]
    neurovisual_status: str
    accommodative_demand_d: Optional[float]
    recommended_follow_up: str
    manufacturing_status: str
    primary_goal: str
    optimization_objective: str
    clinical_recommendation: str
    clinical_guidance: List[str]
    refit: Optional[Dict[str, Any]] = None


class FollowupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_months: float
    followup_days: int
    delta_al: float
    annualized_delta_al: float
    advice: str
    current_support_level: str
    next_support_level: str
    refit_required: bool
    progression_band: str
    eyes: Dict[str, Any]
    comfort_change: Optional[float]
    action_class: str
    responder_status: str
    observed_response_classification: str
    assessment_status: str
    clinical_guidance: List[str]
    csf_change: Optional[float]
    safety_observations: Dict[str, Any]
    deviation_from_original_prediction: Optional[Dict[str, Any]]
    exposure: Dict[str, Any]


class VerificationIn(BaseModel):
    """As-manufactured measurements submitted by the verification station."""
    design_id: str
    manufacturing_id: Optional[str] = None
    sag_error_mm: Optional[float] = None
    element_height_error_mm: Optional[float] = None
    element_position_error_mm: Optional[float] = None
    decentration_mm: Optional[float] = None


class RefitIn(PredictIn):
    """Clinical profile plus the follow-up readings that trigger the refit."""
    previous_design_id: str
    baseline_al: float
    followup_al: float
    interval_months: float


class OverrideIn(BaseModel):
    prediction_id: str
    ai_recommendation: str
    clinician_selection: str
    reason: str
    actor: str = "clinician"


class CaseIn(BaseModel):
    patient_id: str
    indication_code: str = "MYOPIA_CONTROL"
    site_id: str = "SG"
    clinician_id: str


class MeasurementIn(BaseModel):
    measurement_type: str
    value: Any = None
    eye_id: Optional[str] = None
    laterality: Optional[str] = None
    unit: Optional[str] = None
    source: str = "objective"
    device_id: Optional[str] = None
    quality_flag: str = "ACCEPTED"
    missing_data_flag: Optional[bool] = None


class VisitIn(BaseModel):
    visit_code: str
    visit_type: str
    visit_date: str
    clinician_id: str
    site_id: str = "SG"
    measurements: List[MeasurementIn]


class ExposureIn(BaseModel):
    case_id: str
    eye_id: str
    lot_id: str
    exposure_start: str
    exposure_end: Optional[str] = None
    compliance: Optional[str] = None
    replacement_reason: Optional[str] = None


class QcMeasurementIn(BaseModel):
    lot_id: str
    measurement_type: str
    target: Optional[float] = None
    actual: Optional[float] = None
    unit: str
    passed: bool
    measurement_data: Dict[str, Any] = Field(default_factory=dict)


class OutcomeV2In(BaseModel):
    case_id: str
    visit_id: str
    eye_id: str
    baseline_visit_id: str
    exposure_id: str
    baseline_al_mm: float
    followup_al_mm: float
    followup_days: int
    baseline_se_d: Optional[float] = None
    followup_se_d: Optional[float] = None
    baseline_csf: Optional[float] = None
    followup_csf: Optional[float] = None
    comfort_score: Optional[float] = None
    adaptation_score: Optional[float] = None
    compliance: Optional[str] = None
    adverse_event: Optional[str] = None


class EligibilityIn(BaseModel):
    minimum_followup_days: int = 150


class DatasetVersionIn(BaseModel):
    version: str
    case_ids: List[str]
    inclusion_rules: Dict[str, Any]
    schema_version: str = "2.2.1"
    feature_set_version: str = "1.1.0"


class ModelRegisterIn(BaseModel):
    family: str
    version: str
    feature_schema_version: str
    training_dataset_version: str
    algorithm: str
    objective_function_version: str = "1.0.0"
    artifact_uri: Optional[str] = None
    artifact_hash: Optional[str] = None
    code_version: Optional[str] = None
    deployment_context: str = "clinical-pilot"
    indication: str = "MYOPIA_CONTROL"
    initial_status: str = "Development"


class ModelValidationIn(BaseModel):
    gate_results: Dict[str, Any]
    dataset_version: str
    actor: str = "model-validator"


class ModelApprovalIn(BaseModel):
    actor: str
    comment: str


class ModelPromotionIn(BaseModel):
    actor: str


class ModelRollbackIn(BaseModel):
    current_model_id: str
    target_model_id: str
    actor: str
    reason: str


class TrainingRunIn(BaseModel):
    dataset_version_id: str
    model_id: str
    algorithm: str
    hyperparameters: Dict[str, Any]
    random_seed: int
    objective_function_version: str
    code_version: str
    environment_version: str
    metrics: Dict[str, Any]
    artifact_uri: str
    artifact_hash: str


def _authorize_internal(role: str, permission: str, api_key: str) -> None:
    """RBAC plus optional shared-key enforcement for non-clinical V2 APIs.

    Development remains usable without configuration. Production fails closed
    unless ``NSO_INTERNAL_API_KEY`` is set and supplied by the caller.
    """
    configured = os.environ.get("NSO_INTERNAL_API_KEY", "")
    if _is_production and not configured:
        raise HTTPException(status_code=503, detail="Internal API authentication is not configured")
    if configured and api_key != configured:
        raise HTTPException(status_code=401, detail="Invalid internal API key")
    try:
        v2.ClinicalDigitalThread.authorize(role, permission)
    except v2.EngineeringError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _followup_timing(
    interval_months: Optional[float], baseline_date: Optional[str], followup_date: Optional[str]
) -> tuple[float, int]:
    if baseline_date and followup_date:
        try:
            days = (date.fromisoformat(followup_date) - date.fromisoformat(baseline_date)).days
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD") from exc
        if days <= 0:
            raise HTTPException(status_code=422, detail="Follow-up date must be after baseline date")
        return round(days / 30.4375, 3), days
    if interval_months is None or interval_months <= 0:
        raise HTTPException(
            status_code=422,
            detail="Provide a positive interval_months or both baseline_date and followup_date",
        )
    months = float(interval_months)
    return months, max(1, round(months * 30.4375))


def _followup_interval(
    interval_months: Optional[float], baseline_date: Optional[str], followup_date: Optional[str]
) -> float:
    """Compatibility wrapper retained for report and legacy callers."""
    return _followup_timing(interval_months, baseline_date, followup_date)[0]


def _clinical(inp: PredictIn, *, reuse_design: bool = False) -> dict:
    """Run the fitting and return the screened clinical payload."""
    if reuse_design and inp.design_id:
        if not v2.REGISTRY.known(inp.design_id):
            raise HTTPException(status_code=404, detail="Unknown design ID")
        snapshot = v2.REGISTRY._get(inp.design_id).get("clinical_snapshot")
        if snapshot is None:
            raise HTTPException(status_code=409, detail="Design predates report snapshots")
        v2.assert_no_design_leak(snapshot)
        return snapshot
    result = v2.public_clinical_only(inp.to_patient(), patient_id=inp.patient_id)
    return result


@app.post("/api/predict", response_model=ClinicalResponse)
def predict(inp: PredictIn):
    """Clinical layer only. The design recipe stays on the server."""
    return _clinical(inp)


@app.post("/api/followup", response_model=FollowupResponse)
def followup(inp: FollowupIn):
    baseline = inp.baseline_al if inp.baseline_al is not None else inp.baseline_od_al
    current = inp.followup_al if inp.followup_al is not None else inp.followup_od_al
    if baseline is None or current is None:
        raise HTTPException(status_code=422, detail="Baseline and follow-up axial length are required")
    expectation = {}
    if inp.design_id:
        if not v2.REGISTRY.known(inp.design_id):
            raise HTTPException(status_code=404, detail="Unknown design ID")
        expectation = v2.REGISTRY._get(inp.design_id).get("clinical_expectation", {})
    interval_months, followup_days = _followup_timing(
        inp.interval_months, inp.baseline_date, inp.followup_date
    )
    result = v2.clinical_followup(
        baseline, current, interval_months, inp.current_support_level,
        followup_days=followup_days,
        baseline_od_al=inp.baseline_od_al, followup_od_al=inp.followup_od_al,
        baseline_os_al=inp.baseline_os_al, followup_os_al=inp.followup_os_al,
        baseline_comfort=inp.baseline_comfort, current_comfort=inp.current_comfort,
        visual_stress_score=inp.visual_stress_score,
        average_wear_hours=inp.average_wear_hours, compliance=inp.compliance,
        original_fit_score=expectation.get("myopia_management_fit"),
        original_prediction_confidence=expectation.get("prediction_confidence"),
        baseline_csf=inp.baseline_csf, current_csf=inp.current_csf,
        adverse_event=inp.adverse_event, intolerance=inp.intolerance,
    )
    if inp.design_id:
        outcome_id = "OUT-" + hashlib.sha256(
            json.dumps(inp.model_dump(), sort_keys=True, default=str).encode()
        ).hexdigest()[:20].upper()
        design_record = v2.REGISTRY._get(inp.design_id)
        prediction_id = design_record.get("prediction_id")
        prediction_record = (
            v2.REGISTRY._domain_record("prediction", prediction_id)
            if prediction_id else {}
        )
        followup_visit_id = "VIS-" + hashlib.sha256(
            f"{outcome_id}:followup".encode()
        ).hexdigest()[:20].upper()
        case_id = design_record.get("case_id")
        if case_id:
            eye_ids = prediction_record.get("eye_ids", {})
            rows = []
            for eye_key, value in (("OD", inp.followup_od_al), ("OS", inp.followup_os_al)):
                rows.append({
                    "measurement_type": "axial_length",
                    "value": value,
                    "unit": "mm",
                    "eye_id": eye_ids.get(eye_key),
                    "laterality": eye_key,
                    "source": "objective",
                })
            rows.extend([
                {"measurement_type": "comfort", "value": inp.current_comfort, "unit": "score_0_10", "source": "patient-reported"},
                {"measurement_type": "csf", "value": inp.current_csf, "unit": "index", "source": "objective"},
                {"measurement_type": "wear_hours", "value": inp.average_wear_hours, "unit": "hours/day", "source": "behavioral"},
            ])
            try:
                v2.ClinicalDigitalThread(v2.REGISTRY._store_impl).record_visit(
                    case_id=case_id,
                    visit_id=followup_visit_id,
                    visit_code=f"FU-{followup_days}D",
                    visit_type="unscheduled",
                    visit_date=inp.followup_date or date.today().isoformat(),
                    clinician_id="unassigned",
                    site_id=design_record.get("site", "SG"),
                    measurements=rows,
                    actor="clinician",
                )
            except v2.EngineeringError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        v2.REGISTRY._record_outcome(outcome_id, inp.design_id, {
            "outcome_id": outcome_id,
            "design_id": inp.design_id,
            "patient_id": inp.patient_id,
            "raw": inp.model_dump(),
            "derived": result,
        })
        if prediction_id:
            v2.REGISTRY._store_impl.append_related("prediction_outcome", prediction_id, {
                "outcome_id": outcome_id,
                "design_id": inp.design_id,
                "observed_response_classification": result["observed_response_classification"],
            })
    return result


@app.post("/api/clinical/override")
def clinical_override(inp: OverrideIn):
    governance = v2.GovernanceRegistry(v2.REGISTRY._store_impl)
    try:
        return governance.record_override(**inp.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown prediction ID")
    except v2.GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/governance/lineage/{execution_id}")
def execution_lineage(execution_id: str):
    try:
        lineage = v2.GovernanceRegistry(v2.REGISTRY._store_impl).lineage(execution_id)
        v2.assert_no_design_leak(lineage)
        return lineage
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown execution ID")


# --------------------------------------------------------------------------- #
# V2.2 engineering API.  These endpoints operate on immutable normalized
# domain objects.  Existing /api/predict and /api/followup remain as the
# clinician-friendly orchestration layer.
# --------------------------------------------------------------------------- #

def _thread() -> v2.ClinicalDigitalThread:
    return v2.ClinicalDigitalThread(v2.REGISTRY._store_impl)


@app.post("/api/v2/cases")
def create_case(
    inp: CaseIn,
    x_nso_role: str = Header(default="clinician"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "clinical:write", x_internal_api_key)
    try:
        return _thread().create_case(**inp.model_dump(), actor=x_nso_role)
    except (KeyError, v2.EngineeringError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v2/cases/{case_id}/visits")
def create_visit(
    case_id: str,
    inp: VisitIn,
    x_nso_role: str = Header(default="clinician"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "clinical:write", x_internal_api_key)
    try:
        payload = inp.model_dump()
        payload["measurements"] = [m.model_dump() for m in inp.measurements]
        return _thread().record_visit(
            case_id=case_id, **payload, actor=x_nso_role
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown case ID") from exc
    except v2.EngineeringError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v2/trace/{case_id}")
def case_trace(
    case_id: str,
    x_nso_role: str = Header(default="clinician"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "trace:read", x_internal_api_key)
    try:
        return _thread().trace_case(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown case ID") from exc


@app.post("/api/v2/exposures")
def create_exposure(
    inp: ExposureIn,
    x_nso_role: str = Header(default="clinician"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "outcome:write", x_internal_api_key)
    try:
        return _thread().record_exposure(**inp.model_dump(), actor=x_nso_role)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown referenced object: {exc}") from exc
    except v2.EngineeringError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v2/qc-measurements")
def create_qc_measurement(
    inp: QcMeasurementIn,
    x_nso_role: str = Header(default="manufacturing_engineer"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "qc:write", x_internal_api_key)
    try:
        return _thread().record_qc(**inp.model_dump(), actor=x_nso_role)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown manufacturing lot") from exc


@app.post("/api/v2/outcomes")
def create_outcome(
    inp: OutcomeV2In,
    x_nso_role: str = Header(default="clinician"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "outcome:write", x_internal_api_key)
    try:
        return _thread().record_outcome(**inp.model_dump(), actor=x_nso_role)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown referenced object: {exc}") from exc
    except v2.EngineeringError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v2/cases/{case_id}/training-eligibility")
def training_eligibility(
    case_id: str,
    inp: EligibilityIn,
    x_nso_role: str = Header(default="data_scientist"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "dataset:write", x_internal_api_key)
    try:
        return _thread().evaluate_training_eligibility(
            case_id=case_id,
            minimum_followup_days=inp.minimum_followup_days,
            actor=x_nso_role,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown case ID") from exc


@app.post("/api/v2/datasets")
def create_dataset_version(
    inp: DatasetVersionIn,
    x_nso_role: str = Header(default="data_scientist"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "dataset:write", x_internal_api_key)
    governance = v2.GovernanceRegistry(v2.REGISTRY._store_impl)
    try:
        return governance.create_dataset_version(**inp.model_dump(), actor=x_nso_role)
    except v2.GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v2/training-runs")
def create_training_run(
    inp: TrainingRunIn,
    x_nso_role: str = Header(default="ml_engineer"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "training:write", x_internal_api_key)
    try:
        return v2.GovernanceRegistry(v2.REGISTRY._store_impl).record_training_run(
            **inp.model_dump(), actor=x_nso_role
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown referenced object: {exc}") from exc


@app.post("/api/v2/models")
def register_model(
    inp: ModelRegisterIn,
    x_nso_role: str = Header(default="ml_engineer"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "model:write", x_internal_api_key)
    try:
        return v2.GovernanceRegistry(v2.REGISTRY._store_impl).register_model(
            **inp.model_dump(), actor=x_nso_role
        )
    except v2.GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v2/models")
def list_models(
    x_nso_role: str = Header(default="model_validator"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "validation:write", x_internal_api_key)
    governance = v2.GovernanceRegistry(v2.REGISTRY._store_impl)
    return [
        {**record, "status": governance.model_status(record["model_id"])}
        for record in v2.REGISTRY._store_impl.list_records("model")
    ]


@app.post("/api/v2/models/{model_id}/validate")
def validate_model(
    model_id: str,
    inp: ModelValidationIn,
    x_nso_role: str = Header(default="model_validator"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "validation:write", x_internal_api_key)
    try:
        return v2.GovernanceRegistry(v2.REGISTRY._store_impl).validate_candidate(
            model_id, inp.gate_results,
            dataset_version=inp.dataset_version,
            actor=inp.actor,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown model ID") from exc
    except v2.GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v2/models/{model_id}/candidate")
def nominate_model_candidate(
    model_id: str,
    inp: ModelPromotionIn,
    x_nso_role: str = Header(default="ml_engineer"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "model:write", x_internal_api_key)
    try:
        return v2.GovernanceRegistry(v2.REGISTRY._store_impl).nominate_candidate(
            model_id, actor=inp.actor
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown model ID") from exc
    except v2.GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v2/models/{model_id}/approve")
def approve_model(
    model_id: str,
    inp: ModelApprovalIn,
    x_nso_role: str = Header(default="model_validator"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "model:approve", x_internal_api_key)
    try:
        return v2.GovernanceRegistry(v2.REGISTRY._store_impl).approve_model(
            model_id, actor=inp.actor, comment=inp.comment
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown model ID") from exc
    except v2.GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v2/models/{model_id}/promote")
def promote_model(
    model_id: str,
    inp: ModelPromotionIn,
    x_nso_role: str = Header(default="model_validator"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "model:approve", x_internal_api_key)
    try:
        return v2.GovernanceRegistry(v2.REGISTRY._store_impl).promote_model(
            model_id, actor=inp.actor
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown model ID") from exc
    except v2.GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v2/models/rollback")
def rollback_model(
    inp: ModelRollbackIn,
    x_nso_role: str = Header(default="model_validator"),
    x_internal_api_key: str = Header(default=""),
):
    _authorize_internal(x_nso_role, "model:approve", x_internal_api_key)
    try:
        return v2.GovernanceRegistry(v2.REGISTRY._store_impl).rollback_model(**inp.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown model ID: {exc}") from exc
    except v2.GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/design/approve")
def approve_design(inp: ApprovalIn):
    if not v2.REGISTRY.known(inp.design_id):
        raise HTTPException(status_code=404, detail="Unknown design ID")
    return v2.REGISTRY._approve(inp.design_id, actor=inp.actor)


# --------------------------------------------------------------------------- #
# Manufacturing: submit-only from the clinic; segmented pull for vendors.
# --------------------------------------------------------------------------- #

@app.post("/api/manufacturing/submit")
def manufacturing_submit(inp: ManufacturingSubmitIn):
    """Submit a registered design to manufacturing. Returns a job handle.

    Replaces the old "Download Manufacturing CSV": the clinic never holds the
    manufacturing data, it only authorizes the job.
    """
    if not v2.REGISTRY.known(inp.design_id):
        raise HTTPException(status_code=404, detail="Unknown design ID")
    if not v2.REGISTRY._is_approved(inp.design_id):
        raise HTTPException(status_code=409, detail="Design must be approved before manufacturing")
    job = v2.REGISTRY.submit_to_manufacturing(inp.design_id, site=inp.site)
    v2.assert_no_design_leak(job)
    return job


@app.post("/api/manufacturing/package")
def manufacturing_package(
    inp: ManufacturingPackageIn,
    x_api_key: str = Header(default=""),
):
    """Release ONE segment of the manufacturing projection to one vendor.

    Not reachable from the clinical frontend: it requires the shared vendor API
    key, and each segment carries only the geometry its own process step
    executes — never the full recipe or the patient phenotype behind it.
    """
    role = _authorize_vendor(x_api_key, inp.segment)
    if not v2.REGISTRY.known(inp.design_id):
        raise HTTPException(status_code=404, detail="Unknown design ID")
    try:
        return v2.REGISTRY.manufacturing_segments(
            inp.design_id, inp.segment, page=inp.page,
            oem_id=role,
            capability_version=inp.capability_profile_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/manufacturing/verify")
def manufacturing_verify(inp: VerificationIn, x_api_key: str = Header(default="")):
    """Geometric verification of an as-manufactured lens.

    Checks that the machine cut the shape that was sent. It does NOT verify
    optical performance -- the response says so explicitly. Same vendor key as
    the segment API: verification happens on the manufacturing side, not in the
    clinic.
    """
    _authorize_vendor(x_api_key, "AV")
    if not v2.REGISTRY.known(inp.design_id):
        raise HTTPException(status_code=404, detail="Unknown design ID")
    measured = {
        k: val for k, val in inp.model_dump().items()
        if k not in ("design_id", "manufacturing_id") and val is not None
    }
    return v2.geometric_verification(
        inp.design_id, measured, manufacturing_id=inp.manufacturing_id
    )


@app.post("/api/refit", response_model=ClinicalResponse)
def refit(inp: RefitIn):
    """Closed loop: clinical feedback produces a new personalized design."""
    try:
        result = v2.public_clinical(v2.refit(
            inp.to_patient(),
            inp.previous_design_id,
            inp.baseline_al,
            inp.followup_al,
            inp.interval_months,
        ))
        v2.REGISTRY._audit("clinical_response_generated", result["design_id"])
        return result
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown previous design ID")


# --------------------------------------------------------------------------- #
# PDF reports — clinical layer only, same rule as the API responses.
# --------------------------------------------------------------------------- #

def _pdf_response(pdf_bytes: bytes, filename: str) -> Response:
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/report/prediction")
def report_prediction(inp: PredictIn):
    result = _clinical(inp, reuse_design=True)
    pdf = report.build_report(inp.model_dump(), result)
    return _pdf_response(pdf, "nso-report.pdf")


@app.post("/api/report/followup")
def report_followup(inp: FollowupReportIn):
    result = _clinical(inp, reuse_design=True)
    baseline = inp.baseline_al if inp.baseline_al is not None else inp.baseline_od_al
    current = inp.followup_al if inp.followup_al is not None else inp.followup_od_al
    if baseline is None or current is None:
        raise HTTPException(status_code=422, detail="Baseline and follow-up axial length are required")
    expectation = {}
    if inp.design_id:
        if not v2.REGISTRY.known(inp.design_id):
            raise HTTPException(status_code=404, detail="Unknown design ID")
        expectation = v2.REGISTRY._get(inp.design_id).get("clinical_expectation", {})
    interval_months, followup_days = _followup_timing(
        inp.interval_months, inp.baseline_date, inp.followup_date
    )
    fu = v2.clinical_followup(
        baseline, current, interval_months, followup_days=followup_days,
        baseline_od_al=inp.baseline_od_al, followup_od_al=inp.followup_od_al,
        baseline_os_al=inp.baseline_os_al, followup_os_al=inp.followup_os_al,
        baseline_comfort=inp.baseline_comfort, current_comfort=inp.current_comfort,
        visual_stress_score=inp.visual_stress_score_followup,
        average_wear_hours=inp.average_wear_hours, compliance=inp.compliance,
        original_fit_score=expectation.get("myopia_management_fit"),
        original_prediction_confidence=expectation.get("prediction_confidence"),
        baseline_csf=inp.baseline_csf, current_csf=inp.current_csf,
        adverse_event=inp.adverse_event, intolerance=inp.intolerance,
    )
    followup_block = {
        "ctx": {
            "baseline_al": baseline,
            "followup_al": current,
            "baseline_od_al": inp.baseline_od_al,
            "followup_od_al": inp.followup_od_al,
            "baseline_os_al": inp.baseline_os_al,
            "followup_os_al": inp.followup_os_al,
            "interval_months": interval_months,
        },
        "result": fu,
    }
    pdf = report.build_report(inp.model_dump(), result, followup=followup_block)
    return _pdf_response(pdf, "nso-report.pdf")
