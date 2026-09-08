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
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

import nso as v2
import report

app = FastAPI(title="NSO AI-PC Fitting API", version="2.0")

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
        "version": "2.0",
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


class PredictIn(BaseModel):
    patient_id: Optional[str] = None
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
    primary_goal: str = "Myopia Management"

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
    interval_months: int
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


class FollowupReportIn(PredictIn):
    """Full patient inputs (for the prediction body) + the follow-up readings,
    so the follow-up report is the prediction report with a follow-up section."""
    baseline_al: float
    followup_al: float
    interval_months: int


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
    patient_id: str
    clinical_dataset_id: str
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
    out_of_range_measurements: List[Dict[str, Any]]
    neurovisual_status: str
    accommodative_demand_d: Optional[float]
    recommended_follow_up: str
    manufacturing_status: str
    primary_goal: str
    clinical_recommendation: str
    refit: Optional[Dict[str, Any]] = None


class FollowupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    interval_months: int


def _clinical(inp: PredictIn) -> dict:
    """Run the fitting and return the screened clinical payload."""
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
    result = v2.clinical_followup(
        baseline, current, inp.interval_months, inp.current_support_level,
        baseline_od_al=inp.baseline_od_al, followup_od_al=inp.followup_od_al,
        baseline_os_al=inp.baseline_os_al, followup_os_al=inp.followup_os_al,
        baseline_comfort=inp.baseline_comfort, current_comfort=inp.current_comfort,
        visual_stress_score=inp.visual_stress_score,
        average_wear_hours=inp.average_wear_hours, compliance=inp.compliance,
    )
    if inp.design_id:
        outcome_id = "OUT-" + hashlib.sha256(
            json.dumps(inp.model_dump(), sort_keys=True, default=str).encode()
        ).hexdigest()[:20].upper()
        v2.REGISTRY._record_outcome(outcome_id, inp.design_id, {
            "outcome_id": outcome_id,
            "design_id": inp.design_id,
            "patient_id": inp.patient_id,
            "raw": inp.model_dump(),
            "derived": result,
        })
    return result


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
    result = _clinical(inp)
    pdf = report.build_report(inp.model_dump(), result)
    return _pdf_response(pdf, "nso-report.pdf")


@app.post("/api/report/followup")
def report_followup(inp: FollowupReportIn):
    result = _clinical(inp)
    fu = v2.clinical_followup(
        inp.baseline_al, inp.followup_al, inp.interval_months
    )
    followup_block = {
        "ctx": {
            "baseline_al": inp.baseline_al,
            "followup_al": inp.followup_al,
            "interval_months": inp.interval_months,
        },
        "result": fu,
    }
    pdf = report.build_report(inp.model_dump(), result, followup=followup_block)
    return _pdf_response(pdf, "nso-report.pdf")
