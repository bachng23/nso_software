"""V2.2 clinical digital-thread records.

This module implements the engineering contract described by the V2.2
specification on top of the application's immutable ``DesignStore`` seam.  A
production Postgres deployment can project the same records into normalized
tables; SQLite/in-memory deployments retain identical behaviour for tests.

Restricted recipe parameters are deliberately never returned by
``trace_case``.  The trace exposes opaque identifiers, hashes and lifecycle
state only, preserving the clinical black-box boundary.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable


CLINICAL_SCHEMA_VERSION = "2.2.1"
MEASUREMENT_SCHEMA_VERSION = "1.0.0"

VISIT_TYPES = frozenset({
    "baseline", "followup", "1_month", "3_month", "6_month", "12_month", "unscheduled"
})
LATERALITIES = frozenset({"OD", "OS"})
ELIGIBILITY_STATES = frozenset({
    "ELIGIBLE", "PENDING", "INCOMPLETE", "QC_FAILURE",
    "INSUFFICIENT_FOLLOWUP", "PROTOCOL_DEVIATION", "EXCLUDED",
})

ROLE_PERMISSIONS = {
    "clinician": {"clinical:write", "clinical:read", "outcome:write", "trace:read"},
    "clinical_researcher": {"clinical:read", "outcome:read", "trace:read"},
    "optical_engineer": {"design:read", "trace:read"},
    "manufacturing_engineer": {"recipe:read", "manufacturing:write", "qc:write", "trace:read"},
    "data_scientist": {"dataset:write", "training:write", "trace:read"},
    "ml_engineer": {"model:write", "training:write", "trace:read"},
    "model_validator": {"validation:write", "model:approve", "trace:read"},
    "system_admin": {"*"},
    "auditor": {"audit:read", "trace:read"},
}

# Unit and plausibility metadata for the longitudinal minimum dataset.  Missing
# values are valid but explicit; present values outside these bounds are
# retained with a warning instead of silently discarded.
MEASUREMENT_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "axial_length": {"unit": "mm", "min": 18.0, "max": 35.0},
    "sphere": {"unit": "D", "min": -20.0, "max": 15.0},
    "cylinder": {"unit": "D", "min": -10.0, "max": 10.0},
    "axis": {"unit": "deg", "min": 0.0, "max": 180.0},
    "spherical_equivalent": {"unit": "D", "min": -20.0, "max": 15.0},
    "nominal_axis": {"unit": "deg", "min": 0.0, "max": 360.0},
    "settled_rotation": {"unit": "deg", "min": -180.0, "max": 180.0},
    "rotation_sd": {"unit": "deg", "min": 0.0, "max": 90.0},
    "recovery_time": {"unit": "s", "min": 0.0, "max": 600.0},
    "asymmetry_index": {"unit": "index", "min": -10.0, "max": 10.0},
    "temporal_nasal_ratio": {"unit": "ratio", "min": 0.0, "max": 10.0},
    "csf": {"unit": "index", "min": 0.0, "max": 100.0},
    "comfort": {"unit": "score_0_10", "min": 0.0, "max": 10.0},
    "adaptation": {"unit": "score_0_10", "min": 0.0, "max": 10.0},
    "wear_hours": {"unit": "hours/day", "min": 0.0, "max": 24.0},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _opaque(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20].upper()}"


def _stable(prefix: str, payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(blob.encode()).hexdigest()[:20].upper()}"


def _hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


class EngineeringError(ValueError):
    """The requested transition violates the V2.2 engineering contract."""


class ClinicalDigitalThread:
    """Append-only clinical, physical-product and learning lineage service."""

    def __init__(self, store) -> None:
        self.store = store

    @staticmethod
    def authorize(role: str, permission: str) -> None:
        allowed = ROLE_PERMISSIONS.get(role, set())
        if "*" not in allowed and permission not in allowed:
            raise EngineeringError(f"role '{role}' is not permitted to perform '{permission}'")

    def _put(self, domain: str, record_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        self.store.put_record(domain, record_id, record)
        return record

    def _audit(
        self, event_type: str, object_type: str, object_id: str, *,
        actor: str, parent_event_id: str | None = None,
        previous_version: str | None = None, new_version: str | None = None,
        reason: str | None = None, metadata: Dict[str, Any] | None = None,
    ) -> str:
        event_id = _opaque("EVT")
        self.store.append_audit({
            "event_id": event_id,
            "event_type": event_type,
            "action": event_type,
            "actor": actor,
            "object_type": object_type,
            "object_id": object_id,
            "parent_event_id": parent_event_id,
            "previous_version": previous_version,
            "new_version": new_version,
            "reason": reason,
            "metadata": metadata or {},
            "timestamp": _now(),
            "result": "success",
        })
        return event_id

    def create_case(
        self, *, patient_id: str, indication_code: str,
        site_id: str, clinician_id: str, case_id: str | None = None,
        actor: str = "clinician",
    ) -> Dict[str, Any]:
        case_id = case_id or _opaque("CAS")
        try:
            self.store.get_record("patient", patient_id)
        except KeyError:
            self._put("patient", patient_id, {
                "patient_id": patient_id,
                "site_id": site_id,
                "created_at": _now(),
                "pii_separation_required": True,
            })
            self._audit("PATIENT_CREATED", "patient", patient_id, actor=actor)
        record = {
            "case_id": case_id,
            "patient_id": patient_id,
            "indication_code": indication_code,
            "site_id": site_id,
            "clinician_id": clinician_id,
            "status": "OPEN",
            "schema_version": CLINICAL_SCHEMA_VERSION,
            "created_at": _now(),
        }
        self._put("case", case_id, record)
        self._audit("CASE_CREATED", "case", case_id, actor=actor)
        return record

    def ensure_eyes(self, patient_id: str, *, actor: str = "system") -> Dict[str, str]:
        eye_ids: Dict[str, str] = {}
        for laterality in sorted(LATERALITIES):
            eye_id = _stable("EYE", {"patient_id": patient_id, "laterality": laterality})
            eye_ids[laterality] = eye_id
            self._put("eye", eye_id, {
                "eye_id": eye_id,
                "patient_id": patient_id,
                "laterality": laterality,
                "created_at": _now(),
            })
        return eye_ids

    def record_visit(
        self, *, case_id: str, visit_code: str, visit_type: str,
        visit_date: str, clinician_id: str, site_id: str,
        measurements: Iterable[Dict[str, Any]], actor: str = "clinician",
        visit_id: str | None = None,
    ) -> Dict[str, Any]:
        case = self.store.get_record("case", case_id)
        if visit_type not in VISIT_TYPES:
            raise EngineeringError(f"unsupported visit_type: {visit_type}")
        try:
            date.fromisoformat(visit_date)
        except ValueError as exc:
            raise EngineeringError("visit_date must use YYYY-MM-DD") from exc
        visit_id = visit_id or _opaque("VIS")
        record = {
            "visit_id": visit_id,
            "case_id": case_id,
            "patient_id": case["patient_id"],
            "visit_code": visit_code,
            "visit_type": visit_type,
            "visit_date": visit_date,
            "clinician_id": clinician_id,
            "site_id": site_id,
            "schema_version": CLINICAL_SCHEMA_VERSION,
            "created_at": _now(),
        }
        self._put("visit", visit_id, record)
        measurement_ids = []
        for position, item in enumerate(measurements):
            measurement = self.record_measurement(
                visit_id=visit_id,
                case_id=case_id,
                patient_id=case["patient_id"],
                position=position,
                actor=actor,
                **item,
            )
            measurement_ids.append(measurement["measurement_id"])
        self._audit("VISIT_CREATED", "visit", visit_id, actor=actor)
        return {**record, "measurement_ids": measurement_ids}

    def record_measurement(
        self, *, visit_id: str, case_id: str, patient_id: str,
        measurement_type: str, value: Any = None, eye_id: str | None = None,
        laterality: str | None = None, unit: str | None = None,
        source: str = "objective", device_id: str | None = None,
        quality_flag: str = "ACCEPTED", missing_data_flag: bool | None = None,
        position: int = 0, actor: str = "clinician",
    ) -> Dict[str, Any]:
        if laterality is not None and laterality not in LATERALITIES:
            raise EngineeringError("laterality must be OD or OS")
        if eye_id is not None:
            eye = self.store.get_record("eye", eye_id)
            if eye.get("patient_id") != patient_id:
                raise EngineeringError("measurement eye does not belong to the visit patient")
            if laterality is not None and eye.get("laterality") != laterality:
                raise EngineeringError("measurement laterality does not match eye_id")
        definition = MEASUREMENT_DEFINITIONS.get(measurement_type, {})
        expected_unit = definition.get("unit")
        unit = unit or expected_unit or "unspecified"
        warnings = []
        if expected_unit and unit != expected_unit:
            warnings.append(f"expected unit {expected_unit}")
        missing = value is None or bool(missing_data_flag)
        if value is not None and isinstance(value, (int, float)):
            lower, upper = definition.get("min"), definition.get("max")
            if lower is not None and not (lower <= float(value) <= upper):
                warnings.append(f"outside plausible range [{lower}, {upper}]")
        measurement_id = _stable("MEA", {
            "visit_id": visit_id, "type": measurement_type, "eye": eye_id,
            "laterality": laterality, "position": position,
        })
        record = {
            "measurement_id": measurement_id,
            "visit_id": visit_id,
            "case_id": case_id,
            "patient_id": patient_id,
            "eye_id": eye_id,
            "laterality": laterality,
            "measurement_type": measurement_type,
            "value": value,
            "unit": unit,
            "source": source,
            "device_id": device_id,
            "quality_flag": "WARNING" if warnings else quality_flag,
            "missing_data_flag": missing,
            "validation_warnings": warnings,
            "schema_version": MEASUREMENT_SCHEMA_VERSION,
            "recorded_at": _now(),
        }
        self._put("clinical_measurement", measurement_id, record)
        self._audit("MEASUREMENT_RECORDED", "clinical_measurement", measurement_id, actor=actor)
        return record

    def record_feature_vector(
        self, *, case_id: str, visit_id: str, features: Dict[str, Any],
        feature_set_version: str, raw_input: Any, actor: str = "system",
    ) -> Dict[str, Any]:
        feature_vector_id = _opaque("FEA")
        record = {
            "feature_vector_id": feature_vector_id,
            "case_id": case_id,
            "visit_id": visit_id,
            "feature_set_version": feature_set_version,
            "raw_input_hash": _hash(raw_input),
            "features": features,
            "immutable": True,
            "created_at": _now(),
        }
        self._put("feature_vector", feature_vector_id, record)
        self._audit("FEATURE_VECTOR_CREATED", "feature_vector", feature_vector_id, actor=actor)
        return record

    def register_recipe(
        self, *, design_id: str, design_version: str,
        parameters: Dict[str, Any], tolerances: Dict[str, Any] | None = None,
        actor: str = "system",
    ) -> Dict[str, Any]:
        parameter_hash = _hash(parameters)
        recipe_id = _stable("RCP", {"design_id": design_id})
        versions = [r for r in self.store.list_records("recipe_version") if r["recipe_id"] == recipe_id]
        version_number = len(versions) + 1
        recipe_version_id = _stable("RCV", {
            "recipe_id": recipe_id, "version": version_number, "hash": parameter_hash,
        })
        self._put("recipe", recipe_id, {
            "recipe_id": recipe_id,
            "design_id": design_id,
            "created_at": _now(),
            "access_class": "restricted_manufacturing_ip",
        })
        self._put("recipe_version", recipe_version_id, {
            "recipe_version_id": recipe_version_id,
            "recipe_id": recipe_id,
            "version": version_number,
            "design_id": design_id,
            "design_version": design_version,
            "parameters": parameters,
            "tolerances": tolerances or {},
            "parameter_hash": parameter_hash,
            "access_class": "restricted_manufacturing_ip",
            "created_at": _now(),
        })
        self._audit("RECIPE_VERSION_CREATED", "recipe_version", recipe_version_id, actor=actor)
        return {
            "recipe_id": recipe_id,
            "recipe_version_id": recipe_version_id,
            "version": version_number,
            "parameter_hash": parameter_hash,
        }

    def create_manufacturing_lot(
        self, *, recipe_version_id: str, lot_code: str,
        material_code: str, process_version: str, manufacturer_id: str,
        facility_id: str, actor: str = "manufacturing_engineer",
    ) -> Dict[str, Any]:
        recipe_version = self.store.get_record("recipe_version", recipe_version_id)
        lot_id = _opaque("LOT")
        record = {
            "lot_id": lot_id,
            "lot_code": lot_code,
            "recipe_id": recipe_version["recipe_id"],
            "recipe_version_id": recipe_version_id,
            "design_id": recipe_version["design_id"],
            "material_code": material_code,
            "process_version": process_version,
            "manufacturer_id": manufacturer_id,
            "facility_id": facility_id,
            "status": "MANUFACTURED",
            "created_at": _now(),
        }
        self._put("manufacturing_lot", lot_id, record)
        self._audit("MANUFACTURING_LOT_CREATED", "manufacturing_lot", lot_id, actor=actor)
        return record

    def record_qc(
        self, *, lot_id: str, measurement_type: str, target: float | None,
        actual: float | None, unit: str, passed: bool,
        measurement_data: Dict[str, Any] | None = None,
        actor: str = "manufacturing_engineer",
    ) -> Dict[str, Any]:
        self.store.get_record("manufacturing_lot", lot_id)
        qc_id = _opaque("QC")
        record = {
            "qc_id": qc_id,
            "lot_id": lot_id,
            "measurement_type": measurement_type,
            "target": target,
            "actual": actual,
            "unit": unit,
            "measurement_data": measurement_data or {},
            "passed": bool(passed),
            "measured_at": _now(),
        }
        self._put("qc_measurement", qc_id, record)
        self._audit("QC_MEASUREMENT_RECORDED", "qc_measurement", qc_id, actor=actor)
        return record

    def record_exposure(
        self, *, case_id: str, eye_id: str, lot_id: str,
        exposure_start: str, exposure_end: str | None = None,
        compliance: str | None = None, replacement_reason: str | None = None,
        actor: str = "clinician",
    ) -> Dict[str, Any]:
        case = self.store.get_record("case", case_id)
        eye = self.store.get_record("eye", eye_id)
        lot = self.store.get_record("manufacturing_lot", lot_id)
        if eye["patient_id"] != case["patient_id"]:
            raise EngineeringError("eye does not belong to the case patient")
        try:
            start = date.fromisoformat(exposure_start)
            end = date.fromisoformat(exposure_end) if exposure_end else None
        except ValueError as exc:
            raise EngineeringError("exposure dates must use YYYY-MM-DD") from exc
        if end is not None and end < start:
            raise EngineeringError("exposure_end cannot precede exposure_start")
        exposure_id = _opaque("EXP")
        record = {
            "exposure_id": exposure_id,
            "case_id": case_id,
            "eye_id": eye_id,
            "lot_id": lot_id,
            "design_id": lot["design_id"],
            "recipe_version_id": lot["recipe_version_id"],
            "exposure_start": exposure_start,
            "exposure_end": exposure_end,
            "compliance": compliance,
            "replacement_reason": replacement_reason,
            "created_at": _now(),
        }
        self._put("product_exposure", exposure_id, record)
        self._audit("PRODUCT_EXPOSURE_RECORDED", "product_exposure", exposure_id, actor=actor)
        return record

    def record_outcome(
        self, *, case_id: str, visit_id: str, eye_id: str,
        baseline_visit_id: str, exposure_id: str,
        baseline_al_mm: float, followup_al_mm: float,
        followup_days: int, baseline_se_d: float | None = None,
        followup_se_d: float | None = None, baseline_csf: float | None = None,
        followup_csf: float | None = None, comfort_score: float | None = None,
        adaptation_score: float | None = None, compliance: str | None = None,
        adverse_event: str | None = None, actor: str = "clinician",
    ) -> Dict[str, Any]:
        if followup_days <= 0:
            raise EngineeringError("followup_days must be positive")
        case = self.store.get_record("case", case_id)
        visit = self.store.get_record("visit", visit_id)
        baseline_visit = self.store.get_record("visit", baseline_visit_id)
        eye = self.store.get_record("eye", eye_id)
        exposure = self.store.get_record("product_exposure", exposure_id)
        if visit.get("case_id") != case_id or baseline_visit.get("case_id") != case_id:
            raise EngineeringError("both visits must belong to the outcome case")
        if eye.get("patient_id") != case.get("patient_id"):
            raise EngineeringError("outcome eye does not belong to the case patient")
        if exposure.get("case_id") != case_id or exposure.get("eye_id") != eye_id:
            raise EngineeringError("outcome must reference the actual exposure for this case and eye")
        delta_al = float(followup_al_mm) - float(baseline_al_mm)
        annualized = delta_al * 365.25 / float(followup_days)
        outcome_id = _opaque("OUT")
        record = {
            "outcome_id": outcome_id,
            "case_id": case_id,
            "visit_id": visit_id,
            "baseline_visit_id": baseline_visit_id,
            "eye_id": eye_id,
            "exposure_id": exposure_id,
            "followup_days": int(followup_days),
            "baseline_al_mm": float(baseline_al_mm),
            "followup_al_mm": float(followup_al_mm),
            "delta_al_mm": round(delta_al, 4),
            "annualized_delta_al_mm": round(annualized, 4),
            "baseline_se_d": baseline_se_d,
            "followup_se_d": followup_se_d,
            "delta_se_d": (
                round(float(followup_se_d) - float(baseline_se_d), 4)
                if baseline_se_d is not None and followup_se_d is not None else None
            ),
            "baseline_csf": baseline_csf,
            "followup_csf": followup_csf,
            "delta_csf": (
                round(float(followup_csf) - float(baseline_csf), 4)
                if baseline_csf is not None and followup_csf is not None else None
            ),
            "comfort_score": comfort_score,
            "adaptation_score": adaptation_score,
            "compliance": compliance,
            "adverse_event": adverse_event,
            "created_at": _now(),
        }
        self._put("clinical_outcome", outcome_id, record)
        self._audit("CLINICAL_OUTCOME_RECORDED", "clinical_outcome", outcome_id, actor=actor)
        return record

    def evaluate_training_eligibility(
        self, *, case_id: str, minimum_followup_days: int = 150,
        actor: str = "system",
    ) -> Dict[str, Any]:
        outcomes = [r for r in self.store.list_records("clinical_outcome") if r["case_id"] == case_id]
        exposures = [r for r in self.store.list_records("product_exposure") if r["case_id"] == case_id]
        reasons = []
        status = "ELIGIBLE"
        if not outcomes:
            status, reasons = "INCOMPLETE", ["no clinical outcome"]
        elif max(r["followup_days"] for r in outcomes) < minimum_followup_days:
            status, reasons = "INSUFFICIENT_FOLLOWUP", ["minimum follow-up not reached"]
        elif not exposures:
            status, reasons = "INCOMPLETE", ["actual product exposure missing"]
        elif any(e.get("compliance") in {None, "Unknown"} for e in exposures):
            status, reasons = "PENDING", ["compliance assessment missing"]
        elif any(e.get("compliance") == "Poor" for e in exposures):
            status, reasons = "PROTOCOL_DEVIATION", ["poor treatment exposure compliance"]
        else:
            lot_ids = {e["lot_id"] for e in exposures}
            qcs = [q for q in self.store.list_records("qc_measurement") if q["lot_id"] in lot_ids]
            if not qcs:
                status, reasons = "PENDING", ["QC evidence missing"]
            elif any(not q["passed"] for q in qcs):
                status, reasons = "QC_FAILURE", ["one or more QC measurements failed"]
        eligibility_id = _opaque("ELG")
        record = {
            "eligibility_id": eligibility_id,
            "case_id": case_id,
            "status": status,
            "reasons": reasons,
            "minimum_followup_days": minimum_followup_days,
            "evaluated_at": _now(),
        }
        self._put("training_eligibility", eligibility_id, record)
        self._audit("TRAINING_ELIGIBILITY_EVALUATED", "training_eligibility", eligibility_id, actor=actor)
        return record

    def trace_case(self, case_id: str) -> Dict[str, Any]:
        case = self.store.get_record("case", case_id)
        patient_id = case["patient_id"]
        visits = [r for r in self.store.list_records("visit") if r.get("case_id") == case_id]
        visit_ids = {r["visit_id"] for r in visits}
        measurements = [
            r for r in self.store.list_records("clinical_measurement")
            if r.get("visit_id") in visit_ids
        ]
        features = [r for r in self.store.list_records("feature_vector") if r.get("case_id") == case_id]
        inferences = [r for r in self.store.list_records("inference") if r.get("case_id") == case_id]
        design_ids = {r.get("design_id") for r in inferences if r.get("design_id")}
        exposures = [r for r in self.store.list_records("product_exposure") if r.get("case_id") == case_id]
        design_ids.update(r.get("design_id") for r in exposures if r.get("design_id"))
        recipes = [
            {k: v for k, v in r.items() if k not in {"parameters", "tolerances"}}
            for r in self.store.list_records("recipe_version")
            if r.get("design_id") in design_ids
        ]
        recipe_version_ids = {r["recipe_version_id"] for r in recipes}
        lots = [r for r in self.store.list_records("manufacturing_lot") if r.get("recipe_version_id") in recipe_version_ids]
        lot_ids = {r["lot_id"] for r in lots}
        qcs = [r for r in self.store.list_records("qc_measurement") if r.get("lot_id") in lot_ids]
        outcomes = [r for r in self.store.list_records("clinical_outcome") if r.get("case_id") == case_id]
        eligibility = [r for r in self.store.list_records("training_eligibility") if r.get("case_id") == case_id]
        datasets = [
            r for r in self.store.list_records("dataset_version")
            if case_id in r.get("case_ids", [])
        ]
        dataset_version_ids = {r["dataset_version_id"] for r in datasets}
        training_runs = [r for r in self.store.list_records("training_run") if r.get("dataset_version_id") in dataset_version_ids]
        model_ids = {r.get("model_id") for r in inferences if r.get("model_id")}
        model_ids.update(r.get("model_id") for r in training_runs if r.get("model_id"))
        models = [r for r in self.store.list_records("model") if r.get("model_id") in model_ids]
        validations = [r for r in self.store.list_records("model_validation") if r.get("model_id") in model_ids]
        deployments = [r for r in self.store.list_records("model_deployment") if r.get("model_id") in model_ids]
        return {
            "thread": "Clinical Digital Thread V2.2",
            "case": case,
            "patient": {"patient_id": patient_id},
            "eyes": [r for r in self.store.list_records("eye") if r.get("patient_id") == patient_id],
            "visits": visits,
            "measurements": measurements,
            "feature_vectors": features,
            "inferences": inferences,
            "design_ids": sorted(design_ids),
            "recipe_versions": recipes,
            "manufacturing_lots": lots,
            "qc_measurements": qcs,
            "product_exposures": exposures,
            "clinical_outcomes": outcomes,
            "training_eligibility": eligibility,
            "dataset_versions": datasets,
            "training_runs": training_runs,
            "models": models,
            "validations": validations,
            "deployments": deployments,
            "audit_events": [
                event for event in self.store.audits()
                if event.get("object_id") in {
                    case_id, patient_id, *visit_ids, *design_ids, *lot_ids,
                    *(r.get("outcome_id") for r in outcomes),
                }
            ],
            "ip_boundary": "Recipe parameters and tolerances are withheld from this trace.",
        }


__all__ = [
    "CLINICAL_SCHEMA_VERSION", "ClinicalDigitalThread", "ELIGIBILITY_STATES",
    "EngineeringError", "LATERALITIES", "MEASUREMENT_DEFINITIONS",
    "MEASUREMENT_SCHEMA_VERSION", "ROLE_PERMISSIONS", "VISIT_TYPES",
]
