"""Acceptance coverage for the supervisor's V2.2 clinical-pilot brief."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api
import nso


def payload(**extra):
    body = {
        "patient_id": "PAT-V22-ACCEPTANCE",
        "age": 12,
        "od": {"sphere": -2.5, "axial_length": 24.8},
        "os": {"sphere": -2.25, "axial_length": 24.7},
        "optimization_objective": "Myopia Control",
    }
    body.update(extra)
    return body


def test_v22_prediction_has_governed_identity_and_honest_probability_gate():
    result = TestClient(api.app).post("/api/predict", json=payload()).json()
    assert result["design_version"] == "V1"
    assert result["prediction_id"].startswith("PRD-")
    assert result["execution_id"].startswith("EXE-")
    assert result["optimization_objective"] == "Myopia Control"
    assert result["prediction_confidence_category"] in {"Low", "Moderate", "High"}
    # The currently deployed rule engine has no clinically calibrated
    # probability model, so precise percentages must not be fabricated.
    assert result["predicted_response"]["distribution"] is None
    assert result["predicted_response"]["display_allowed"] is False
    assert len(result["key_clinical_contributors"]) >= 3


def test_each_generate_has_a_unique_id_and_report_reuses_visible_design():
    client = TestClient(api.app)
    first = client.post("/api/predict", json=payload(patient_id="PAT-V22-UNIQUE")).json()
    second = client.post("/api/predict", json=payload(patient_id="PAT-V22-UNIQUE")).json()
    assert first["design_id"] != second["design_id"]
    report = client.post("/api/report/prediction", json={
        **payload(patient_id="PAT-V22-UNIQUE"),
        "design_id": first["design_id"],
    })
    assert report.status_code == 200
    assert report.content.startswith(b"%PDF")
    # Exporting an existing result must not create a third design.
    assert nso.REGISTRY.known(first["design_id"])
    assert nso.REGISTRY.known(second["design_id"])


def test_out_of_range_measurement_suppresses_precise_response_distribution():
    result = TestClient(api.app).post(
        "/api/predict", json=payload(patient_id="PAT-V22-OOD", photopic_pupil=20)
    ).json()
    assert result["prediction_state"] == "Outside Validated Range"
    assert result["predicted_response"]["display_allowed"] is False
    assert result["clinical_recommendation"].startswith("Extended assessment")


def test_dates_drive_interval_and_followup_has_observed_outcome_fields():
    client = TestClient(api.app)
    prediction = client.post(
        "/api/predict", json=payload(patient_id="PAT-V22-FOLLOWUP")
    ).json()
    response = client.post("/api/followup", json={
        "design_id": prediction["design_id"],
        "patient_id": prediction["patient_id"],
        "baseline_od_al": 24.8,
        "followup_od_al": 24.86,
        "baseline_os_al": 24.7,
        "followup_os_al": 24.75,
        "baseline_date": "2026-01-01",
        "followup_date": "2026-07-01",
        "baseline_csf": 70,
        "current_csf": 66,
        "compliance": "Good",
    })
    assert response.status_code == 200
    result = response.json()
    assert result["interval_months"] == pytest.approx(5.947, abs=0.01)
    assert result["observed_response_classification"] in {
        "Good", "Moderate", "Limited", "Insufficient Data",
    }
    assert result["assessment_status"] == "Observed Clinical Outcome"
    assert result["csf_change"] == -4
    assert result["clinical_guidance"]


def test_short_followup_is_insufficient_data_and_adverse_event_forces_review():
    result = nso.clinical_followup(
        24.8, 24.81, 1,
        adverse_event="Transient discomfort",
        compliance="Good",
    )
    assert result["observed_response_classification"] == "Insufficient Data"
    assert result["assessment_status"] == "Insufficient Data"
    assert result["action_class"] == "ADJUST"


def test_fractional_date_interval_can_generate_immutable_v2_recommendation():
    client = TestClient(api.app)
    baseline = client.post(
        "/api/predict", json=payload(patient_id="PAT-V22-NEXT")
    ).json()
    observed = client.post("/api/followup", json={
        "design_id": baseline["design_id"],
        "baseline_od_al": 24.8, "followup_od_al": 24.9,
        "baseline_os_al": 24.7, "followup_os_al": 24.78,
        "baseline_date": "2026-01-01", "followup_date": "2026-07-01",
    }).json()
    revised = client.post("/api/refit", json={
        **payload(patient_id=baseline["patient_id"]),
        "previous_design_id": baseline["design_id"],
        "baseline_al": 24.8,
        "followup_al": 24.9,
        "interval_months": observed["interval_months"],
    })
    assert revised.status_code == 200
    v2 = revised.json()
    assert v2["design_version"] == "V2"
    assert v2["design_id"] == baseline["design_id"] + "-R1"
    assert v2["refit"]["previous_design_id"] == baseline["design_id"]


def test_execution_record_carries_all_five_version_chains_and_lineage():
    client = TestClient(api.app)
    prediction = client.post(
        "/api/predict", json=payload(patient_id="PAT-V22-LINEAGE")
    ).json()
    lineage_response = client.get(
        f"/api/governance/lineage/{prediction['execution_id']}"
    )
    assert lineage_response.status_code == 200
    lineage = lineage_response.json()
    execution = lineage["execution"]
    for key in (
        "software_version", "clinical_schema_version", "model_version",
        "design_version", "dataset_version", "feature_schema_version",
    ):
        assert execution[key]
    assert lineage["thread"] == "Clinical Digital Thread"
    assert lineage["prediction_id"] == prediction["prediction_id"]
    assert lineage["design_id"] == prediction["design_id"]


def test_model_candidate_requires_all_gates_and_never_auto_deploys():
    store = nso.InMemoryDesignStore()
    governance = nso.GovernanceRegistry(store)
    model = governance.register_model(
        family="pilot-model", version="1.0.0", feature_schema_version="3.0.0"
    )
    assert governance.model_status(model["model_id"]) == "Candidate"
    with pytest.raises(nso.GovernanceError, match="validation gates mismatch"):
        governance.validate_candidate(
            model["model_id"], {"data_quality": True}, dataset_version="pilot-1"
        )
    failed = governance.validate_candidate(
        model["model_id"],
        {gate: {"passed": gate != "clinical_performance"} for gate in nso.VALIDATION_GATES},
        dataset_version="pilot-1",
    )
    assert failed["passed"] is False
    assert governance.model_status(model["model_id"]) == "Retired"
    with pytest.raises(nso.GovernanceError, match="validated candidate"):
        governance.promote_model(model["model_id"], actor="medical-director")


def test_validated_model_needs_explicit_human_promotion():
    store = nso.InMemoryDesignStore()
    governance = nso.GovernanceRegistry(store)
    model = governance.register_model(
        family="pilot-model", version="2.0.0", feature_schema_version="3.0.0"
    )
    passed = governance.validate_candidate(
        model["model_id"],
        {gate: {"passed": True} for gate in nso.VALIDATION_GATES},
        dataset_version="pilot-2",
        actor="validation-board",
    )
    assert passed["passed"] is True
    assert governance.model_status(model["model_id"]) == "Validated"
    governance.promote_model(model["model_id"], actor="medical-director")
    assert governance.model_status(model["model_id"]) == "Production"


def test_retraining_trigger_creates_candidate_permission_not_deployment():
    governance = nso.GovernanceRegistry(nso.InMemoryDesignStore())
    before = governance.retraining_trigger(
        observation_count=499, dataset_version="pilot-1"
    )
    after = governance.retraining_trigger(
        observation_count=500, dataset_version="pilot-2"
    )
    assert before["candidate_allowed"] is False
    assert after["candidate_allowed"] is True
    assert before["automatic_deployment"] is False
    assert after["automatic_deployment"] is False


def test_clinician_override_requires_reason_and_is_append_only():
    client = TestClient(api.app)
    prediction = client.post(
        "/api/predict", json=payload(patient_id="PAT-V22-OVERRIDE")
    ).json()
    missing_reason = client.post("/api/clinical/override", json={
        "prediction_id": prediction["prediction_id"],
        "ai_recommendation": "Continue",
        "clinician_selection": "Adjust",
        "reason": "",
    })
    assert missing_reason.status_code == 422
    recorded = client.post("/api/clinical/override", json={
        "prediction_id": prediction["prediction_id"],
        "ai_recommendation": "Continue",
        "clinician_selection": "Adjust",
        "reason": "Comfort declined during adaptation",
    })
    assert recorded.status_code == 200
    assert recorded.json()["eligible_as_learning_signal"] is True


def test_frontend_uses_v22_clinical_language_and_no_false_probabilities():
    source = (Path(__file__).parents[2] / "src/frontend/app/page.js").read_text()
    layout = (Path(__file__).parents[2] / "src/frontend/app/layout.js").read_text()
    assert "V2.2 · Clinical Decision Support" in source
    assert "13 core clinical data groups · sufficient for initial recommendation" in source
    assert "Optimization objective" in source
    assert "MODEL PREDICTION · PRE-TREATMENT ESTIMATE" in source
    assert "OBSERVED CLINICAL OUTCOME · CLOSED LOOP" in source
    assert "Generate Next Recommendation" in source
    assert "Precise response probabilities are withheld" in source
    assert "NSO AI-PC V2.2 · Clinical Decision Support" in layout


def test_v22_public_payload_and_lineage_contain_no_restricted_design_data():
    client = TestClient(api.app)
    prediction = client.post(
        "/api/predict", json=payload(patient_id="PAT-V22-BLACKBOX")
    ).json()
    lineage = client.get(
        f"/api/governance/lineage/{prediction['execution_id']}"
    ).json()
    for payload_value in (prediction, lineage):
        nso.assert_no_design_leak(payload_value)
        serialized = json.dumps(payload_value).lower()
        for forbidden in ("fill_factor", "element_height", "toolpath", "recipe"):
            assert forbidden not in serialized
