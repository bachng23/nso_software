"""Acceptance tests for the supervisor's NSO AI-PC V2.1 optimization brief."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api
import nso
from nso.clinical import CLINICAL_RESPONSE_FIELDS
from nso.manufacturing import DesignRegistry, InMemoryDesignStore
from nso.features import accommodative_stress_index, task_load_index


def payload(age=12.25):
    return {
        "age": age,
        "od": {"sphere": -2.5, "axial_length": 24.8},
        "os": {"sphere": -2.25, "axial_length": 24.7},
        "near_phoria": -3,
        "csf_band": "Normal",
        "near_hours": 7,
        "digital_hours": 4,
        "primary_goal": "Balanced Optimization",
    }


def test_clinical_api_is_an_explicit_allowlist():
    result = TestClient(api.app).post("/api/predict", json=payload()).json()
    assert set(result) == CLINICAL_RESPONSE_FIELDS
    assert result["refit"] is None
    body = json.dumps(result).lower()
    for internal in ("candidate", "relative_loss", "pairs_evaluated", "recipe"):
        assert internal not in body
    assert set(result["predicted"]) == {
        "myopia_management_fit", "visual_comfort", "adaptation",
        "binocular_compatibility",
    }


def test_design_handle_has_at_least_96_bits_and_revision_is_immutable():
    client = TestClient(api.app)
    baseline = client.post("/api/predict", json=payload(12.5)).json()
    assert re.fullmatch(r"NSO-[0-9A-F]{24}", baseline["design_id"])
    revised = client.post("/api/refit", json={
        **payload(12.5), "previous_design_id": baseline["design_id"],
        "baseline_al": 24.8, "followup_al": 25.0, "interval_months": 6,
    }).json()
    assert revised["design_id"] == baseline["design_id"] + "-R1"
    assert nso.REGISTRY.known(baseline["design_id"])
    assert nso.REGISTRY.known(revised["design_id"])


def test_store_rejects_overwrite_and_returns_copies():
    store = InMemoryDesignStore()
    store.put("D1", {"revision": 0})
    snapshot = store.get("D1")
    snapshot["revision"] = 99
    assert store.get("D1")["revision"] == 0
    with pytest.raises(ValueError, match="immutable"):
        store.put("D1", {"revision": 1})


def test_approval_locks_gate_before_manufacturing():
    client = TestClient(api.app)
    design = client.post("/api/predict", json=payload(12.75)).json()
    blocked = client.post("/api/manufacturing/submit", json={"design_id": design["design_id"]})
    assert blocked.status_code == 409
    approved = client.post("/api/design/approve", json={"design_id": design["design_id"]})
    assert approved.status_code == 200
    assert client.post(
        "/api/manufacturing/submit", json={"design_id": design["design_id"]}
    ).status_code == 200


def test_followup_is_per_eye_and_uses_only_three_action_classes():
    result = nso.clinical_followup(
        24.8, 25.0, 6,
        baseline_od_al=24.8, followup_od_al=25.0,
        baseline_os_al=24.7, followup_os_al=24.75,
        baseline_comfort=8, current_comfort=6,
        average_wear_hours=9, compliance="Good",
    )
    assert result["eyes"]["OD"]["delta_al"] == pytest.approx(0.2)
    assert result["eyes"]["OS"]["delta_al"] == pytest.approx(0.05)
    assert result["comfort_change"] == -2
    assert result["action_class"] in {
        "CONTINUE", "ADJUST", "RE-FIT",
    }


def test_all_three_followup_action_classes_are_reachable():
    maintain = nso.clinical_followup(24.8, 24.82, 12)
    optimize = nso.clinical_followup(24.8, 25.0, 6)
    review = nso.clinical_followup(24.8, 24.82, 12, compliance="Poor")
    assert maintain["action_class"] == "CONTINUE"
    assert optimize["action_class"] == "RE-FIT"
    assert review["action_class"] == "ADJUST"


def test_followup_closes_loop_against_server_stored_prediction():
    client = TestClient(api.app)
    design = client.post("/api/predict", json=payload(13.1)).json()
    response = client.post("/api/followup", json={
        "design_id": design["design_id"],
        "baseline_od_al": 24.8, "followup_od_al": 25.0,
        "baseline_os_al": 24.7, "followup_os_al": 24.75,
        "interval_months": 6, "compliance": "Good",
    })
    assert response.status_code == 200
    result = response.json()
    assert set(result["eyes"]) == {"OD", "OS"}
    assert result["responder_status"] == "Suboptimal responder"
    assert result["action_class"] == "RE-FIT"
    deviation = result["deviation_from_original_prediction"]
    assert deviation["original_fit_score"] == design["predicted"]["myopia_management_fit"]
    assert deviation["observed_responder_classification"] == result["responder_status"]
    assert deviation["direction"] in {"Aligned", "More favorable", "Less favorable"}


def test_followup_unknown_design_cannot_supply_an_unverified_prediction():
    response = TestClient(api.app).post("/api/followup", json={
        "design_id": "NSO-000000000000000000000000",
        "baseline_al": 24.8, "followup_al": 24.9, "interval_months": 6,
    })
    assert response.status_code == 404


def test_supervisor_copy_and_medical_claim_disclaimer_are_present():
    source = (Path(__file__).parents[2] / "src/frontend/app/page.js").read_text()
    assert "V2.1 · Clinical Decision Support — Knowledge-guided personalization engine" in source
    assert "13 clinical data groups" in source
    assert "This score estimates design–phenotype compatibility and does not predict treatment efficacy or axial-length reduction." in source
    assert "not yet used" not in source.lower()
    assert "V2 · Research Prototype" not in source


def test_processing_stages_appear_after_ten_seconds_and_api_is_pre_warmed():
    source = (Path(__file__).parents[2] / "src/frontend/app/page.js").read_text()
    assert "PROCESSING_STAGES" in source
    assert "}, 10000);" in source
    assert "fetch(`${API}/api/health`" in source


def test_design_id_uses_server_side_keyed_digest():
    source = (Path(__file__).parent / "nso/design/identity.py").read_text()
    assert "hmac.new" in source
    assert "DESIGN_ID_SECRET" in source
    frontend = (Path(__file__).parents[2] / "src/frontend/app/page.js").read_text()
    assert "DESIGN_ID_SECRET" not in frontend


def test_digital_time_is_a_fraction_not_an_independent_near_exposure():
    low = nso.PatientInput(near_hours=8, digital_hours=2)
    high = nso.PatientInput(near_hours=8, digital_hours=7)
    assert accommodative_stress_index(low) == accommodative_stress_index(high)
    assert task_load_index(low) == task_load_index(high)


def test_frontend_source_contains_no_restricted_design_vocabulary():
    source = (Path(__file__).parents[2] / "src/frontend/app/page.js").read_text().lower()
    for term in (
        "recipe", "microstructure", "fill factor", "spatial density", "jitter",
        "temporal asymmetry", "relative_loss", "pairs_evaluated", "binocular_cost",
    ):
        assert term not in source


def test_vault_and_audit_are_separated_and_audit_has_no_recipe():
    registry = DesignRegistry(InMemoryDesignStore())
    registry.register("NSO-ABC", {"design_id": "NSO-ABC", "revision": 0, "recipes": []})
    metadata = registry._store_impl.get("NSO-ABC")
    assert "recipes" not in metadata
    assert metadata["vault_id"].startswith("VLT-")
    audit = registry._audit_records("NSO-ABC")
    assert audit and "recipe" not in json.dumps(audit).lower()


def test_existing_patient_with_new_dataset_audits_profile_modification():
    client = TestClient(api.app)
    first = client.post("/api/predict", json={**payload(13.25), "patient_id": "PAT-AUDIT-V21"}).json()
    second = client.post("/api/predict", json={
        **payload(13.5), "patient_id": first["patient_id"],
    }).json()
    assert first["clinical_dataset_id"] != second["clinical_dataset_id"]
    actions = {event["action"] for event in nso.REGISTRY._audit_records("PAT-AUDIT-V21")}
    assert {"patient_profile_created", "patient_profile_modified"} <= actions


def test_oem_package_is_versioned_and_expires():
    registry = DesignRegistry(InMemoryDesignStore())
    patient = nso.PatientInput(
        age=13, od=nso.EyeInput(sphere=-2), os=nso.EyeInput(sphere=-2)
    )
    design = nso.run_fitting(patient)["design"]
    # Copy the authorized internal design into an isolated registry.
    registry.register(design["design_id"], design)
    package = registry.manufacturing_segments(
        design["design_id"], "AV", oem_id="assembly", capability_version="1.0"
    )
    assert package["oem_capability_version"] == "1.0"
    assert package["projection_version"] == "2.1"
    assert package["expires_at"] > package["issued_at"]
    with pytest.raises(ValueError, match="capability version"):
        registry.manufacturing_segments(
            design["design_id"], "AV", oem_id="assembly", capability_version="9.0"
        )
