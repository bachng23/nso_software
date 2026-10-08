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


SOFTWARE_VERSION = "2.2.1"
CLINICAL_SCHEMA_VERSION = "2.2.1"
DATASET_VERSION = "pilot-0"
PRODUCT_FAMILIES = frozenset({"CL", "GL", "IOL", "XR"})
MODEL_STATUSES = (
    "Development", "Candidate", "Validated", "Production", "Retired", "Rejected"
)
VALIDATION_GATES = (
    "data_quality",
    "internal_performance",
    "clinical_performance",
    "subgroup_validation",
    "robustness",
    "manufacturing_feasibility",
    "safety_guardrails",
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

    def _audit(self, event_type: str, object_type: str, object_id: str, actor: str, **metadata) -> None:
        self.store.append_audit({
            "event_id": _opaque_id("EVT"),
            "event_type": event_type,
            "action": event_type,
            "actor": actor,
            "object_type": object_type,
            "object_id": object_id,
            "metadata": metadata,
            "timestamp": _now(),
            "result": "success",
        })

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
        algorithm: str = "unspecified",
        objective_function_version: str = "1.0.0",
        artifact_uri: str | None = None,
        artifact_hash: str | None = None,
        code_version: str | None = None,
        deployment_context: str = "clinical-pilot",
        indication: str = "MYOPIA_CONTROL",
        initial_status: str = "Candidate",
        actor: str = "system",
    ) -> Dict[str, Any]:
        if initial_status not in MODEL_STATUSES:
            raise GovernanceError(f"unknown model status: {initial_status}")
        model_id = model_id_for(family, version)
        record = {
            "model_id": model_id,
            "model_family": family,
            "model_version": version,
            "algorithm": algorithm,
            "training_dataset_version": training_dataset_version,
            "patient_count": int(patient_count),
            "eye_count": int(eye_count),
            "feature_schema_version": feature_schema_version,
            "features": sorted(set(features)),
            "target": target,
            "objective_function_version": objective_function_version,
            "artifact_uri": artifact_uri,
            "artifact_hash": artifact_hash,
            "code_version": code_version or SOFTWARE_VERSION,
            "deployment_context": deployment_context,
            "indication": indication,
            "metrics": metrics or {},
            "subgroup_metrics": subgroup_metrics or {},
            "calibrated_probabilities": bool(calibrated_probabilities),
            "initial_status": initial_status,
            "created_at": _now(),
        }
        self.store.put_record("model", model_id, record)
        self._audit("MODEL_VERSION_REGISTERED", "model", model_id, actor)
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
                algorithm="knowledge-guided deterministic rules",
                target="relative_design_phenotype_compatibility",
                calibrated_probabilities=False,
                initial_status="Candidate",
            )

    def model_status(self, model_id: str) -> str:
        model = self.store.get_record("model", model_id)
        transitions = self.store.related("model_status", model_id)
        return transitions[-1]["status"] if transitions else model["initial_status"]

    def nominate_candidate(self, model_id: str, *, actor: str) -> Dict[str, Any]:
        if self.model_status(model_id) != "Development":
            raise GovernanceError("only a development model can become a candidate")
        event = {"status": "Candidate", "actor": actor, "timestamp": _now()}
        self.store.append_related("model_status", model_id, event)
        self._audit("MODEL_CANDIDATE_NOMINATED", "model", model_id, actor)
        return event

    def validate_candidate(
        self,
        model_id: str,
        gate_results: Dict[str, Dict[str, Any] | bool],
        *,
        dataset_version: str,
        actor: str = "validator",
    ) -> Dict[str, Any]:
        self.store.get_record("model", model_id)
        if self.model_status(model_id) != "Candidate":
            raise GovernanceError("only a candidate model can enter validation")
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
                    "status": "Rejected",
                    "actor": actor,
                    "timestamp": _now(),
                    "reason": "validation_failed",
                },
            )
        self._audit(
            "MODEL_VALIDATION_COMPLETED", "model", model_id, actor,
            validation_id=validation_id, passed=passed,
        )
        return record

    def approve_model(
        self, model_id: str, *, actor: str, comment: str
    ) -> Dict[str, Any]:
        if self.model_status(model_id) != "Validated":
            raise GovernanceError("only a validated model can be approved")
        if not comment.strip():
            raise GovernanceError("model approval requires a comment")
        approval_id = _opaque_id("APR")
        record = {
            "approval_id": approval_id,
            "model_id": model_id,
            "actor": actor,
            "comment": comment.strip(),
            "approved_at": _now(),
        }
        self.store.put_record("model_approval", approval_id, record)
        self.store.append_related(
            "model_approval_link", model_id,
            {"approval_id": approval_id, "actor": actor, "timestamp": record["approved_at"]},
        )
        self._audit("MODEL_APPROVED", "model", model_id, actor, approval_id=approval_id)
        return record

    def promote_model(self, model_id: str, *, actor: str) -> Dict[str, Any]:
        if self.model_status(model_id) != "Validated":
            raise GovernanceError("only a validated candidate can enter production")
        approvals = self.store.related("model_approval_link", model_id)
        if not approvals:
            raise GovernanceError("production promotion requires human approval")
        model = self.store.get_record("model", model_id)
        for other in self.production_models():
            if (
                other["model_id"] != model_id
                and other["model_family"] == model["model_family"]
                and other.get("indication") == model.get("indication")
                and other.get("deployment_context") == model.get("deployment_context")
            ):
                self.retire_model(other["model_id"], actor=actor)
        event = {"status": "Production", "actor": actor, "timestamp": _now()}
        self.store.append_related("model_status", model_id, event)
        self._audit("MODEL_PROMOTED", "model", model_id, actor)
        deployment_id = _opaque_id("DEP")
        self.store.put_record("model_deployment", deployment_id, {
            "deployment_id": deployment_id,
            "model_id": model_id,
            "model_family": model["model_family"],
            "indication": model.get("indication"),
            "deployment_context": model.get("deployment_context"),
            "action": "PROMOTE",
            "actor": actor,
            "timestamp": event["timestamp"],
        })
        return event

    def retire_model(self, model_id: str, *, actor: str) -> Dict[str, Any]:
        self.store.get_record("model", model_id)
        event = {"status": "Retired", "actor": actor, "timestamp": _now()}
        self.store.append_related("model_status", model_id, event)
        self._audit("MODEL_RETIRED", "model", model_id, actor)
        return event

    def production_models(self) -> list[Dict[str, Any]]:
        return [
            {**record, "status": self.model_status(record["model_id"])}
            for record in self.store.list_records("model")
            if self.model_status(record["model_id"]) == "Production"
        ]

    def retraining_trigger(
        self, *, observation_count: int, threshold: int = 50,
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
        self._audit(
            "RETRAINING_TRIGGER_EVALUATED", "retraining_trigger", record["trigger_id"], actor,
            candidate_allowed=record["candidate_allowed"],
        )
        return record

    def create_dataset_version(
        self, *, version: str, case_ids: Iterable[str], inclusion_rules: Dict[str, Any],
        schema_version: str, feature_set_version: str, actor: str = "data-scientist",
    ) -> Dict[str, Any]:
        case_ids = sorted(set(case_ids))
        for case_id in case_ids:
            evaluations = [
                row for row in self.store.list_records("training_eligibility")
                if row.get("case_id") == case_id
            ]
            if not evaluations or evaluations[-1].get("status") != "ELIGIBLE":
                raise GovernanceError(
                    f"case {case_id} is not eligible for a frozen training dataset"
                )
        payload_hash = hashlib.sha256(json.dumps({
            "case_ids": case_ids,
            "inclusion_rules": inclusion_rules,
            "schema_version": schema_version,
            "feature_set_version": feature_set_version,
        }, sort_keys=True).encode()).hexdigest()
        dataset_version_id = _stable_id("DSV", {"version": version, "hash": payload_hash})
        record = {
            "dataset_version_id": dataset_version_id,
            "version": version,
            "case_ids": case_ids,
            "case_count": len(case_ids),
            "inclusion_rules": inclusion_rules,
            "schema_version": schema_version,
            "feature_set_version": feature_set_version,
            "content_hash": payload_hash,
            "immutable": True,
            "actor": actor,
            "created_at": _now(),
        }
        self.store.put_record("dataset_version", dataset_version_id, record)
        self._audit("DATASET_VERSION_FROZEN", "dataset_version", dataset_version_id, actor)
        return record

    def record_training_run(
        self, *, dataset_version_id: str, model_id: str,
        algorithm: str, hyperparameters: Dict[str, Any], random_seed: int,
        objective_function_version: str, code_version: str,
        environment_version: str, metrics: Dict[str, Any],
        artifact_uri: str, artifact_hash: str,
        actor: str = "ml-engineer",
    ) -> Dict[str, Any]:
        self.store.get_record("dataset_version", dataset_version_id)
        self.store.get_record("model", model_id)
        training_run_id = _opaque_id("TRN")
        record = {
            "training_run_id": training_run_id,
            "dataset_version_id": dataset_version_id,
            "model_id": model_id,
            "algorithm": algorithm,
            "hyperparameters": hyperparameters,
            "random_seed": int(random_seed),
            "objective_function_version": objective_function_version,
            "code_version": code_version,
            "environment_version": environment_version,
            "metrics": metrics,
            "artifact_uri": artifact_uri,
            "artifact_hash": artifact_hash,
            "status": "COMPLETED",
            "actor": actor,
            "created_at": _now(),
        }
        self.store.put_record("training_run", training_run_id, record)
        self._audit("TRAINING_RUN_COMPLETED", "training_run", training_run_id, actor)
        return record

    def champion_candidate_report(
        self, *, champion_model_id: str, candidate_model_id: str,
        validation_id: str,
    ) -> Dict[str, Any]:
        champion = self.store.get_record("model", champion_model_id)
        candidate = self.store.get_record("model", candidate_model_id)
        validation = self.store.get_record("model_validation", validation_id)
        if validation["model_id"] != candidate_model_id:
            raise GovernanceError("validation does not belong to candidate model")
        report_id = _opaque_id("CMP")
        record = {
            "comparison_id": report_id,
            "champion_model_id": champion_model_id,
            "candidate_model_id": candidate_model_id,
            "validation_id": validation_id,
            "champion_metrics": champion.get("metrics", {}),
            "candidate_metrics": candidate.get("metrics", {}),
            "candidate_passed": validation["passed"],
            "automatic_deployment": False,
            "created_at": _now(),
        }
        self.store.put_record("model_comparison", report_id, record)
        return record

    def rollback_model(
        self, *, current_model_id: str, target_model_id: str,
        actor: str, reason: str,
    ) -> Dict[str, Any]:
        if not reason.strip():
            raise GovernanceError("rollback requires a reason")
        if self.model_status(current_model_id) != "Production":
            raise GovernanceError("current model is not in production")
        target_status = self.model_status(target_model_id)
        if target_status not in {"Validated", "Retired"}:
            raise GovernanceError("rollback target must be validated or previously retired")
        self.retire_model(current_model_id, actor=actor)
        self.store.append_related(
            "model_status", target_model_id,
            {"status": "Production", "actor": actor, "timestamp": _now(), "reason": reason},
        )
        deployment_id = _opaque_id("DEP")
        record = {
            "deployment_id": deployment_id,
            "model_id": target_model_id,
            "previous_model_id": current_model_id,
            "action": "ROLLBACK",
            "actor": actor,
            "reason": reason.strip(),
            "timestamp": _now(),
        }
        self.store.put_record("model_deployment", deployment_id, record)
        self._audit(
            "MODEL_ROLLED_BACK", "model", target_model_id, actor,
            previous_model_id=current_model_id, deployment_id=deployment_id,
        )
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
        self._audit(
            "INFERENCE_EXECUTED", "execution", execution["execution_id"], "system",
            model_id=model_id, prediction_id=prediction_id, design_id=design_id,
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
        self._audit(
            "CLINICIAN_OVERRIDE_RECORDED", "prediction", prediction_id, actor,
            override_id=record["override_id"],
        )
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
