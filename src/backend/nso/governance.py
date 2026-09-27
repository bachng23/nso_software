"""Governed clinical/model records for the V2.2 clinical digital thread.

The design store already provides immutable domain records and append-only
related/audit records.  This module gives those primitives a typed, validated
meaning without creating a second database or a second source of truth.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable


SOFTWARE_VERSION = "2.2.0"
CLINICAL_SCHEMA_VERSION = "2.2.0"
DATASET_VERSION = "pilot-0"
PRODUCT_FAMILIES = frozenset({"CL", "GL", "IOL", "XR"})
MODEL_STATUSES = ("Candidate", "Validated", "Production", "Retired")
VALIDATION_GATES = (
    "data_quality",
    "internal_performance",
    "clinical_performance",
    "subgroup_validation",
    "safety_guardrails",
    "human_approval",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _opaque_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20].upper()}"


def _stable_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()[:20].upper()
    return f"{prefix}-{digest}"


def model_id_for(family: str, version: str) -> str:
    safe_family = re.sub(r"[^A-Za-z0-9]+", "-", family).strip("-").upper()
    safe_version = re.sub(r"[^A-Za-z0-9.]+", "-", version).strip("-").upper()
    return f"MOD-{safe_family}-{safe_version}"


class GovernanceError(ValueError):
    """A governed transition would violate an immutable workflow rule."""


class GovernanceRegistry:
    """Model governance and clinical lineage over a :class:`DesignStore`."""

    def __init__(self, store) -> None:
        self.store = store

    def register_model(
        self,
        *,
        family: str,
        version: str,
        feature_schema_version: str,
        training_dataset_version: str = DATASET_VERSION,
        patient_count: int = 0,
        eye_count: int = 0,
        features: Iterable[str] = (),
        target: str = "design_phenotype_compatibility",
        metrics: Dict[str, Any] | None = None,
        subgroup_metrics: Dict[str, Any] | None = None,
        calibrated_probabilities: bool = False,
        initial_status: str = "Candidate",
    ) -> Dict[str, Any]:
        if initial_status not in MODEL_STATUSES:
            raise GovernanceError(f"unknown model status: {initial_status}")
        model_id = model_id_for(family, version)
        record = {
            "model_id": model_id,
            "model_family": family,
            "model_version": version,
            "training_dataset_version": training_dataset_version,
            "patient_count": int(patient_count),
            "eye_count": int(eye_count),
            "feature_schema_version": feature_schema_version,
            "features": sorted(set(features)),
            "target": target,
            "metrics": metrics or {},
            "subgroup_metrics": subgroup_metrics or {},
            "calibrated_probabilities": bool(calibrated_probabilities),
            "initial_status": initial_status,
            "created_at": _now(),
        }
        self.store.put_record("model", model_id, record)
        return record

    def ensure_runtime_model(self, predictor) -> Dict[str, Any]:
        model_id = model_id_for(predictor.name, predictor.version)
        try:
            return self.store.get_record("model", model_id)
        except KeyError:
            return self.register_model(
                family=predictor.name,
                version=predictor.version,
                feature_schema_version=predictor.schema_version,
                target="relative_design_phenotype_compatibility",
                calibrated_probabilities=False,
                # This rule engine is the already-deployed V2 lineage source.
                # It is production software, but explicitly not a calibrated
                # clinical probability model.
                initial_status="Production",
            )

    def model_status(self, model_id: str) -> str:
        model = self.store.get_record("model", model_id)
        transitions = self.store.related("model_status", model_id)
        return transitions[-1]["status"] if transitions else model["initial_status"]

    def validate_candidate(
        self,
        model_id: str,
        gate_results: Dict[str, Dict[str, Any] | bool],
        *,
        dataset_version: str,
        actor: str = "validator",
    ) -> Dict[str, Any]:
        self.store.get_record("model", model_id)
        missing = set(VALIDATION_GATES) - set(gate_results)
        extra = set(gate_results) - set(VALIDATION_GATES)
        if missing or extra:
            raise GovernanceError(
                f"validation gates mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
            )
        normalized = {
            name: value if isinstance(value, dict) else {"passed": bool(value)}
            for name, value in gate_results.items()
        }
        passed = all(bool(result.get("passed")) for result in normalized.values())
        validation_id = _opaque_id("VAL")
        record = {
            "validation_id": validation_id,
            "model_id": model_id,
            "dataset_version": dataset_version,
            "gates": normalized,
            "passed": passed,
            "actor": actor,
            "timestamp": _now(),
        }
        self.store.put_record("model_validation", validation_id, record)
        self.store.append_related(
            "model_validation_link", model_id,
            {"validation_id": validation_id, "passed": passed},
        )
        if passed:
            self.store.append_related(
                "model_status", model_id,
                {"status": "Validated", "actor": actor, "timestamp": _now()},
            )
        else:
            self.store.append_related(
                "model_status", model_id,
                {
                    "status": "Retired",
                    "actor": actor,
                    "timestamp": _now(),
                    "reason": "validation_failed",
                },
            )
        return record

    def promote_model(self, model_id: str, *, actor: str) -> Dict[str, Any]:
        if self.model_status(model_id) != "Validated":
            raise GovernanceError("only a validated candidate can enter production")
        event = {"status": "Production", "actor": actor, "timestamp": _now()}
        self.store.append_related("model_status", model_id, event)
        self.store.append_audit({
            "timestamp": event["timestamp"], "actor": actor,
            "action": "model_promoted", "object_type": "model",
            "object_id": model_id, "version": model_id, "result": "success",
        })
        return event

    def retire_model(self, model_id: str, *, actor: str) -> Dict[str, Any]:
        self.store.get_record("model", model_id)
        event = {"status": "Retired", "actor": actor, "timestamp": _now()}
        self.store.append_related("model_status", model_id, event)
        return event

    def production_models(self) -> list[Dict[str, Any]]:
        return [
            {**record, "status": self.model_status(record["model_id"])}
            for record in self.store.list_records("model")
            if self.model_status(record["model_id"]) == "Production"
        ]

    def retraining_trigger(
        self, *, observation_count: int, threshold: int = 500,
        dataset_version: str, actor: str = "system",
    ) -> Dict[str, Any]:
        record = {
            "trigger_id": _opaque_id("TRG"),
            "observation_count": int(observation_count),
            "threshold": int(threshold),
            "dataset_version": dataset_version,
            "candidate_allowed": observation_count >= threshold,
            "automatic_deployment": False,
            "actor": actor,
            "timestamp": _now(),
        }
        self.store.put_record("retraining_trigger", record["trigger_id"], record)
        return record

    def record_execution(
        self,
        *,
        patient_id: str,
        eye_ids: Dict[str, str],
        visit_id: str,
        design_id: str,
        design_version: str,
        prediction_id: str,
        model_id: str,
        model_version: str,
        feature_schema_version: str,
        config_version: str,
        product_family: str = "CL",
        dataset_version: str = DATASET_VERSION,
    ) -> Dict[str, Any]:
        if product_family not in PRODUCT_FAMILIES:
            raise GovernanceError(f"unknown product family: {product_family}")
        execution = {
            "execution_id": _opaque_id("EXE"),
            "patient_id": patient_id,
            "eye_ids": dict(eye_ids),
            "visit_id": visit_id,
            "design_id": design_id,
            "design_version": design_version,
            "prediction_id": prediction_id,
            "model_id": model_id,
            "model_version": model_version,
            "dataset_version": dataset_version,
            "feature_schema_version": feature_schema_version,
            "clinical_schema_version": CLINICAL_SCHEMA_VERSION,
            "config_version": config_version,
            "software_version": SOFTWARE_VERSION,
            "product_family": product_family,
            "timestamp": _now(),
        }
        self.store.put_record("execution", execution["execution_id"], execution)
        self.store.append_related(
            "patient_execution", patient_id,
            {"execution_id": execution["execution_id"], "prediction_id": prediction_id},
        )
        return execution

    def record_override(
        self,
        *,
        prediction_id: str,
        ai_recommendation: str,
        clinician_selection: str,
        reason: str,
        actor: str,
    ) -> Dict[str, Any]:
        self.store.get_record("prediction", prediction_id)
        if not reason.strip():
            raise GovernanceError("a clinician override requires a reason")
        record = {
            "override_id": _opaque_id("OVR"),
            "prediction_id": prediction_id,
            "ai_recommendation": ai_recommendation,
            "clinician_selection": clinician_selection,
            "reason": reason.strip(),
            "actor": actor,
            "timestamp": _now(),
            "eligible_as_learning_signal": True,
        }
        self.store.put_record("clinician_override", record["override_id"], record)
        self.store.append_audit({
            "timestamp": record["timestamp"], "actor": actor,
            "action": "clinician_override_recorded", "object_type": "prediction",
            "object_id": prediction_id, "version": SOFTWARE_VERSION,
            "result": "success",
        })
        return record

    def lineage(self, execution_id: str) -> Dict[str, Any]:
        execution = self.store.get_record("execution", execution_id)
        prediction = self.store.get_record("prediction", execution["prediction_id"])
        treatments = [
            record for record in self.store.list_records("treatment")
            if record.get("prediction_id") == execution["prediction_id"]
        ]
        outcomes = [
            record for record in self.store.list_records("outcome")
            if record.get("prediction_id") == execution["prediction_id"]
        ]
        return {
            "thread": "Clinical Digital Thread",
            "patient_id": execution["patient_id"],
            "eye_ids": execution["eye_ids"],
            "visit_id": execution["visit_id"],
            "raw_record_id": prediction["raw_record_id"],
            "preprocessing_version": prediction["preprocessing_version"],
            "feature_schema_version": execution["feature_schema_version"],
            "model_id": execution["model_id"],
            "prediction_id": execution["prediction_id"],
            "design_id": execution["design_id"],
            "product_family": execution["product_family"],
            "treatments": treatments,
            "outcomes": outcomes,
            "next_predictions": self.store.related(
                "next_prediction", execution["prediction_id"]
            ),
            "execution": execution,
        }


__all__ = [
    "CLINICAL_SCHEMA_VERSION", "DATASET_VERSION", "GovernanceError",
    "GovernanceRegistry", "MODEL_STATUSES", "PRODUCT_FAMILIES",
    "SOFTWARE_VERSION", "VALIDATION_GATES", "model_id_for",
]
