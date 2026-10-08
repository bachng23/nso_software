"""End-to-end acceptance tests for the final V2.2 engineering specification."""

from fastapi.testclient import TestClient

import api
import nso


def _prediction(client: TestClient):
    response = client.post("/api/predict", json={
        "patient_id": "PAT-ENGINEERING-E2E",
        "age": 11,
        "od": {
            "sphere": -2.5,
            "cylinder": -0.5,
            "axis": 90,
            "axial_length": 24.7,
            "orientation_id": "ORI-OD-01",
            "nominal_axis_deg": 90,
            "settled_rotation_deg": 3.0,
            "rotation_sd_deg": 1.2,
            "recovery_time_s": 4.5,
            "asymmetry_index": 0.2,
            "temporal_nasal_ratio": 1.1,
        },
        "os": {
            "sphere": -2.25,
            "cylinder": -0.25,
            "axis": 85,
            "axial_length": 24.6,
            "orientation_id": "ORI-OS-01",
            "nominal_axis_deg": 85,
            "settled_rotation_deg": -2.0,
            "rotation_sd_deg": 1.0,
            "recovery_time_s": 4.0,
            "asymmetry_index": 0.1,
            "temporal_nasal_ratio": 0.9,
        },
        "optimization_objective": "Myopia Control",
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_generate_creates_normalized_clinical_lineage_and_restricted_recipe():
    client = TestClient(api.app)
    prediction = _prediction(client)
    trace_response = client.get(f"/api/v2/trace/{prediction['case_id']}")
    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert len(trace["eyes"]) == 2
    assert len(trace["visits"]) == 1
    assert len(trace["measurements"]) >= 20
    assert len(trace["feature_vectors"]) == 1
    assert len(trace["inferences"]) == 1
    assert len(trace["recipe_versions"]) == 1
    assert "parameters" not in trace["recipe_versions"][0]
    assert "tolerances" not in trace["recipe_versions"][0]
    measurement_types = {row["measurement_type"] for row in trace["measurements"]}
    assert {
        "orientation_id", "nominal_axis", "settled_rotation", "rotation_sd",
        "recovery_time", "asymmetry_index", "temporal_nasal_ratio",
    } <= measurement_types


def test_full_case_to_validated_learning_trace():
    client = TestClient(api.app)
    prediction = _prediction(client)
    case_id = prediction["case_id"]
    design_id = prediction["design_id"]

    assert client.post("/api/design/approve", json={
        "design_id": design_id, "actor": "clinical-lead",
    }).status_code == 200
    manufactured = client.post("/api/manufacturing/submit", json={
        "design_id": design_id, "site": "SG",
    })
    assert manufactured.status_code == 200, manufactured.text
    job = manufactured.json()
    assert job["lot_id"].startswith("LOT-")

    qc = nso.geometric_verification(design_id, {
        "sag_error_mm": 0.001,
        "element_height_error_mm": 0.0001,
        "element_position_error_mm": 0.001,
        "decentration_mm": 0.02,
    }, manufacturing_id=job["job_id"])
    assert qc["verdict"] == "Pass"

    trace = client.get(f"/api/v2/trace/{case_id}").json()
    eyes = {eye["laterality"]: eye["eye_id"] for eye in trace["eyes"]}
    baseline_visit_id = trace["visits"][0]["visit_id"]
    exposures = {}
    for eye in ("OD", "OS"):
        response = client.post("/api/v2/exposures", json={
            "case_id": case_id,
            "eye_id": eyes[eye],
            "lot_id": job["lot_id"],
            "exposure_start": "2026-01-01",
            "compliance": "Good",
        })
        assert response.status_code == 200, response.text
        exposures[eye] = response.json()["exposure_id"]

    followup = client.post(f"/api/v2/cases/{case_id}/visits", json={
        "visit_code": "M6",
        "visit_type": "6_month",
        "visit_date": "2026-07-01",
        "clinician_id": "CLINICIAN-1",
        "site_id": "SG",
        "measurements": [
            {"measurement_type": "axial_length", "value": 24.76, "unit": "mm", "eye_id": eyes["OD"], "laterality": "OD"},
            {"measurement_type": "axial_length", "value": 24.65, "unit": "mm", "eye_id": eyes["OS"], "laterality": "OS"},
        ],
    })
    assert followup.status_code == 200, followup.text
    followup_visit_id = followup.json()["visit_id"]

    for eye, baseline, observed in (("OD", 24.7, 24.76), ("OS", 24.6, 24.65)):
        outcome = client.post("/api/v2/outcomes", json={
            "case_id": case_id,
            "visit_id": followup_visit_id,
            "eye_id": eyes[eye],
            "baseline_visit_id": baseline_visit_id,
            "exposure_id": exposures[eye],
            "baseline_al_mm": baseline,
            "followup_al_mm": observed,
            "followup_days": 181,
            "baseline_se_d": -2.5,
            "followup_se_d": -2.6,
            "baseline_csf": 70,
            "followup_csf": 68,
            "comfort_score": 8,
            "adaptation_score": 8,
            "compliance": "Good",
        })
        assert outcome.status_code == 200, outcome.text
        expected = round((observed - baseline) * 365.25 / 181, 4)
        assert outcome.json()["annualized_delta_al_mm"] == expected

    eligibility = client.post(
        f"/api/v2/cases/{case_id}/training-eligibility",
        json={"minimum_followup_days": 150},
    )
    assert eligibility.status_code == 200
    assert eligibility.json()["status"] == "ELIGIBLE"

    dataset = client.post("/api/v2/datasets", json={
        "version": "pilot-e2e-1",
        "case_ids": [case_id],
        "inclusion_rules": {"eligibility": "ELIGIBLE"},
    }).json()
    model = client.post("/api/v2/models", json={
        "family": "e2e-candidate",
        "version": "1.0.0",
        "feature_schema_version": "1.1.0",
        "training_dataset_version": dataset["version"],
        "algorithm": "gradient-boosted-trees",
        "initial_status": "Candidate",
    }).json()
    training = client.post("/api/v2/training-runs", json={
        "dataset_version_id": dataset["dataset_version_id"],
        "model_id": model["model_id"],
        "algorithm": "gradient-boosted-trees",
        "hyperparameters": {"max_depth": 3},
        "random_seed": 42,
        "objective_function_version": "1.0.0",
        "code_version": "test-sha",
        "environment_version": "test-env",
        "metrics": {"mae": 0.08},
        "artifact_uri": "object://models/e2e-1",
        "artifact_hash": "sha256:test",
    })
    assert training.status_code == 200, training.text
    validation = client.post(f"/api/v2/models/{model['model_id']}/validate", json={
        "gate_results": {gate: {"passed": True} for gate in nso.VALIDATION_GATES},
        "dataset_version": dataset["version"],
        "actor": "validation-board",
    })
    assert validation.status_code == 200, validation.text
    assert client.post(f"/api/v2/models/{model['model_id']}/approve", json={
        "actor": "medical-director", "comment": "Independent gates passed.",
    }).status_code == 200
    assert client.post(f"/api/v2/models/{model['model_id']}/promote", json={
        "actor": "medical-director",
    }).status_code == 200

    final_trace = client.get(f"/api/v2/trace/{case_id}").json()
    assert len(final_trace["manufacturing_lots"]) == 1
    assert len(final_trace["qc_measurements"]) == 4
    assert len(final_trace["product_exposures"]) == 2
    assert len(final_trace["clinical_outcomes"]) == 2
    assert final_trace["training_eligibility"][-1]["status"] == "ELIGIBLE"
    assert final_trace["dataset_versions"][0]["dataset_version_id"] == dataset["dataset_version_id"]
    assert final_trace["training_runs"][0]["model_id"] == model["model_id"]
    assert any(item["model_id"] == model["model_id"] for item in final_trace["models"])
    assert any(item["model_id"] == model["model_id"] for item in final_trace["validations"])
    assert any(item["model_id"] == model["model_id"] for item in final_trace["deployments"])


def test_model_promotion_requires_separate_approval_and_rollback_is_auditable():
    store = nso.InMemoryDesignStore()
    governance = nso.GovernanceRegistry(store)
    previous = governance.register_model(
        family="rollback-family", version="1", feature_schema_version="1.1.0"
    )
    candidate = governance.register_model(
        family="rollback-family", version="2", feature_schema_version="1.1.0"
    )
    gates = {gate: {"passed": True} for gate in nso.VALIDATION_GATES}
    for model in (previous, candidate):
        governance.validate_candidate(model["model_id"], gates, dataset_version="ds")
        governance.approve_model(model["model_id"], actor="validator", comment="passed")
    governance.promote_model(previous["model_id"], actor="director")
    governance.promote_model(candidate["model_id"], actor="director")
    assert governance.model_status(previous["model_id"]) == "Retired"
    rollback = governance.rollback_model(
        current_model_id=candidate["model_id"],
        target_model_id=previous["model_id"],
        actor="director",
        reason="post-deployment drift",
    )
    assert rollback["action"] == "ROLLBACK"
    assert governance.model_status(previous["model_id"]) == "Production"
    assert governance.model_status(candidate["model_id"]) == "Retired"
