"""
Tests for the NSO V2 layer: multi-domain phenotyping, the IP boundary, the
design engine and the manufacturing API.

The most important group is "IP boundary" — those tests are the executable
form of the architecture rule "the browser must not receive the recipe".
"""

import json

import pytest
from fastapi.testclient import TestClient

import api
import nso_v2 as v2


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def client():
    return TestClient(api.app)


# One key per vendor role, as production configures them.
_ALL_ROLE_KEYS = {
    "secret": "internal",
    "fs-key": "front_surface",
    "bs-key": "back_surface",
    "av-key": "assembly",
}


def quick_payload(**over):
    """A minimal Tier-1 (Quick Fitting) request body."""
    body = {
        "age": 11,
        "od": {"sphere": -3.25, "cylinder": -0.5, "axis": 180, "axial_length": 25.1},
        "os": {"sphere": -3.0, "cylinder": -0.25, "axis": 175, "axial_length": 24.9},
        "photopic_pupil": 5.2,
        "near_phoria": -4,
        "npc": 9,
        "accommodative_lag": 1.1,
        "csf_band": "Mid",
        "visual_stress_score": 6,
        "near_hours": 7,
        "digital_hours": 5,
        "outdoor_hours": 0.8,
        "primary_goal": "Myopia Management",
    }
    body.update(over)
    return body


def patient(**over):
    b = quick_payload(**over)
    od = v2.EyeInput(**b.pop("od"))
    os_ = v2.EyeInput(**b.pop("os"))
    return v2.PatientInput(od=od, os=os_, **b)


# --------------------------------------------------------------------------- #
# IP boundary — the architecture rule, enforced
# --------------------------------------------------------------------------- #

# Every optical term that identifies the recipe. If any of these ever shows up
# in a clinical payload the design has escaped the server.
LEAK_TERMS = [
    "sa_strength", "sa_profile", "microstructure", "fill_factor",
    "spatial_density", "jitter", "temporal_nasal", "temporal_multiplier",
    "target_mtf", "mtf_reduction", "toolpath", "surface_map", "freeform",
    "recipe",
]


def _assert_clean(blob: str):
    lowered = blob.lower()
    for term in LEAK_TERMS:
        assert term not in lowered, f"design term '{term}' leaked into the payload"


def test_clinical_payload_contains_no_design_terms():
    _assert_clean(json.dumps(v2.clinical_only(patient())))


def test_predict_endpoint_response_contains_no_design_terms(client):
    resp = client.post("/api/predict", json=quick_payload())
    assert resp.status_code == 200
    _assert_clean(resp.text)


def test_predict_endpoint_ignores_client_supplied_design_parameters(client):
    """A client that tries to steer the design gets the same answer as one that
    does not — the old sa_strength/density overrides are gone."""
    baseline = client.post("/api/predict", json=quick_payload()).json()
    injected = client.post(
        "/api/predict",
        json=quick_payload(sa_strength=9.0, density=100, fill_factor_pct=90),
    ).json()
    assert injected["design_id"] == baseline["design_id"]
    assert injected["predicted"] == baseline["predicted"]


def test_assert_no_design_leak_catches_a_nested_offender():
    with pytest.raises(v2.DesignLeakError):
        v2.assert_no_design_leak({"eyes": {"OD": {"sa_strength": 5.0}}})


def test_assert_no_design_leak_catches_offender_inside_a_list():
    with pytest.raises(v2.DesignLeakError):
        v2.assert_no_design_leak({"candidates": [{"ok": 1}, {"fill_factor_pct": 30}]})


def test_registry_has_no_full_recipe_read_path():
    """The registry deliberately exposes only segmented reads."""
    public = {m for m in dir(v2.REGISTRY) if not m.startswith("_")}
    assert public == {
        "backend", "register", "known", "record_job", "record_verification",
        "link_revision", "submit_to_manufacturing", "manufacturing_segments",
    }
    # The write paths return nothing that could carry a recipe. (Annotations
    # are strings here: the module uses `from __future__ import annotations`.)
    for method in ("record_job", "record_verification"):
        assert getattr(v2.DesignRegistry, method).__annotations__["return"] == "None"


def test_no_route_serves_a_design_recipe_or_csv():
    paths = {r.path for r in api.app.routes}
    for p in paths:
        assert "csv" not in p.lower()
        assert "recipe" not in p.lower()
        assert "design/export" not in p.lower()


# --------------------------------------------------------------------------- #
# AI-derived indices
# --------------------------------------------------------------------------- #

def test_all_indices_present_and_in_range():
    idx = v2.ai_derived_indices(patient())
    assert set(idx) == {
        "refractive_risk", "binocular_load", "accommodative_stress",
        "spatial_frequency_sensitivity", "visual_stress", "neural_adaptation",
        "dynamic_robustness", "interocular_image_balance",
    }
    for k, val in idx.items():
        assert 0.0 <= val <= 100.0, k


def test_refractive_risk_increases_with_axial_length():
    low = v2.refractive_risk_index(patient(od={"sphere": -1, "axial_length": 23.0},
                                           os={"sphere": -1, "axial_length": 23.0}))
    high = v2.refractive_risk_index(patient(od={"sphere": -1, "axial_length": 26.0},
                                            os={"sphere": -1, "axial_length": 26.0}))
    assert high > low


def test_refractive_risk_decreases_with_age():
    young = v2.refractive_risk_index(patient(age=7))
    older = v2.refractive_risk_index(patient(age=17))
    assert young > older


def test_binocular_load_increases_with_exophoria_and_receded_npc():
    easy = v2.binocular_load_index(patient(near_phoria=0, npc=5))
    hard = v2.binocular_load_index(patient(near_phoria=-10, npc=14))
    assert hard > easy


def test_binocular_load_uses_optional_reserves_when_present():
    """Sheard's criterion: the reserve only registers when it fails to cover
    twice the deviation. A large exophoria with a small opposing reserve is
    strained; the same exophoria with an ample reserve is not."""
    strained = v2.binocular_load_index(patient(near_phoria=-12, pfv=6))
    compensated = v2.binocular_load_index(patient(near_phoria=-12, pfv=30))
    assert strained > compensated


def test_a_small_deviation_is_compensated_by_either_reserve():
    """2 x 1 prism dioptre is covered by any plausible reserve, so the reserve
    should not move the score at all."""
    low = v2.binocular_load_index(patient(near_phoria=-4, pfv=8))
    high = v2.binocular_load_index(patient(near_phoria=-4, pfv=30))
    assert low == high


def test_accommodative_stress_increases_with_lag():
    assert v2.accommodative_stress_index(patient(accommodative_lag=1.8)) > \
           v2.accommodative_stress_index(patient(accommodative_lag=0.25))


def test_spatial_frequency_index_follows_the_band():
    lo = v2.spatial_frequency_sensitivity_index(patient(csf_band="Low"))
    mid = v2.spatial_frequency_sensitivity_index(patient(csf_band="Mid"))
    hi = v2.spatial_frequency_sensitivity_index(patient(csf_band="High"))
    assert lo < mid < hi


def test_measured_csf_triplet_overrides_the_band():
    p = patient(csf_band="Low", csf_low=95, csf_mid=95, csf_high=95)
    assert v2.spatial_frequency_sensitivity_index(p) > 80


def test_high_frequency_rolloff_costs_more_than_the_mean():
    flat = v2.spatial_frequency_sensitivity_index(patient(csf_low=70, csf_mid=70, csf_high=70))
    steep = v2.spatial_frequency_sensitivity_index(patient(csf_low=100, csf_mid=70, csf_high=40))
    assert steep < flat


def test_visual_stress_index_increases_with_reported_stress():
    assert v2.visual_stress_index(patient(visual_stress_score=9)) > \
           v2.visual_stress_index(patient(visual_stress_score=1))


def test_interocular_balance_drops_with_anisometropia():
    balanced = v2.interocular_image_balance_index(
        patient(od={"sphere": -3, "axial_length": 25.0},
                os={"sphere": -3, "axial_length": 25.0}))
    aniso = v2.interocular_image_balance_index(
        patient(od={"sphere": -3, "axial_length": 25.0},
                os={"sphere": -6, "axial_length": 26.2}))
    assert balanced == 100.0
    assert aniso < balanced


def test_comfort_value_derives_from_stress_when_comfort_absent():
    assert patient(visual_stress_score=2).comfort_value() == 80.0
    assert patient(visual_stress_score=2, visual_comfort_score=5).comfort_value() == 50.0


# --------------------------------------------------------------------------- #
# Visual phenotype
# --------------------------------------------------------------------------- #

def test_phenotype_code_shape():
    p = patient()
    ph = v2.visual_phenotype(p, v2.ai_derived_indices(p))
    parts = ph["code"].split("-")
    assert [x[0] for x in parts] == list(v2.PHENOTYPE_DOMAINS)
    assert all(x[1] in "123" for x in parts)


def test_phenotype_reports_all_five_domains():
    p = patient()
    ph = v2.visual_phenotype(p, v2.ai_derived_indices(p))
    assert [d["key"] for d in ph["domains"]] == list(v2.PHENOTYPE_DOMAINS)


def test_spatial_domain_is_inverted_so_high_always_means_demanding():
    """S grade should rise as contrast sensitivity falls."""
    def s_score(band):
        p = patient(csf_band=band)
        ph = v2.visual_phenotype(p, v2.ai_derived_indices(p))
        return next(d["score"] for d in ph["domains"] if d["key"] == "S")
    assert s_score("Low") > s_score("High")


@pytest.mark.parametrize("score,grade", [(0, 1), (39.9, 1), (40, 2), (69.9, 2), (70, 3), (100, 3)])
def test_grade_boundaries(score, grade):
    assert v2._grade(score) == grade


# --------------------------------------------------------------------------- #
# Design engine (server-side)
# --------------------------------------------------------------------------- #

def test_design_recipe_is_produced_for_both_eyes():
    design = v2.run_fitting(patient())["design"]
    assert [r.eye for r in design["recipes"]] == ["OD", "OS"]


def test_design_geometry_stays_physically_plausible():
    for r in v2.run_fitting(patient())["design"]["recipes"]:
        assert 1.5 <= r.nso_peak_target_d <= 9.0
        assert 0 < r.mean_fill_factor_pct <= 60
        assert r.nso_modulation.spatial_jitter_deg > 0
        assert 0.3 <= r.target_mtf_modulation <= 1.0
        for zone in r.nso_modulation.zones:
            assert 10 <= zone.element_diameter_um <= 50
            assert 0.3 <= zone.element_height_um <= 4.0
            assert 10 <= zone.fill_factor_pct <= 55


def test_high_visual_stress_reduces_optical_load():
    """The optical TARGET is a tier property, so stress shows up in the
    realized geometry rather than in the target label."""
    calm = v2.run_fitting(patient(visual_stress_score=1))["design"]["recipes"][0]
    stressed = v2.run_fitting(patient(visual_stress_score=10))["design"]["recipes"][0]
    assert (stressed.nso_modulation.zone("A").element_height_um
            < calm.nso_modulation.zone("A").element_height_um)


def test_anisometropia_gives_the_eyes_different_base_surfaces():
    """The prescriptions differ, definitionally."""
    od, os_ = v2.run_fitting(
        patient(od={"sphere": -1.0, "axial_length": 23.4},
                os={"sphere": -7.0, "axial_length": 26.4}))["design"]["recipes"]
    assert od.base_surface.sphere != os_.base_surface.sphere


def test_the_joint_optimizer_pulls_an_anisometropic_pair_together():
    """What the binocular penalty is for.

    Fitted independently, these two eyes would receive markedly different
    coverage. Chosen as a pair, the optimizer trades some monocular optimality
    to keep the two lenses close enough to fuse — so the RESULT of a working
    penalty is a more symmetric pair, not a less symmetric one.
    """
    p = patient(od={"sphere": -1.0, "axial_length": 23.4},
                os={"sphere": -7.0, "axial_length": 26.4})
    indices = v2.ai_derived_indices(p)

    # Each eye's own best, ignoring the other.
    od_alone = v2.generate_candidates(p, "OD", indices)[0]["recipe"]
    os_alone = v2.generate_candidates(p, "OS", indices)[0]["recipe"]
    apart = abs(od_alone.mean_fill_factor_pct - os_alone.mean_fill_factor_pct)

    # The pair the joint optimizer actually chooses.
    od, os_ = v2.run_fitting(p)["design"]["recipes"]
    together = abs(od.mean_fill_factor_pct - os_.mean_fill_factor_pct)

    assert together < apart, "the binocular penalty is not pulling the pair together"


def test_candidates_are_ranked_by_loss_and_first_is_selected():
    result = v2.run_fitting(patient())
    rows = result["clinical"]["candidate_comparison"]["OD"]
    losses = [r["relative_loss"] for r in rows]
    assert losses == sorted(losses)
    assert rows[0]["selected"] is True
    assert sum(1 for r in rows if r["selected"]) == 1


def test_candidate_comparison_exposes_outcomes_not_parameters():
    rows = v2.run_fitting(patient())["clinical"]["candidate_comparison"]["OD"]
    for row in rows:
        assert set(row) == {
            "candidate", "predicted_control", "predicted_comfort",
            "predicted_adaptation", "predicted_acuity_retention", "feasible",
            "relative_loss", "selected", "monocular_best",
        }


# --------------------------------------------------------------------------- #
# Design ID
# --------------------------------------------------------------------------- #

def test_design_id_format():
    did = v2.clinical_only(patient())["design_id"]
    assert did.startswith("NSO-")
    assert len(did) == len("NSO-") + 24
    assert all(c in "0123456789ABCDEF" for c in did[4:])


def test_design_id_is_deterministic_for_the_same_patient():
    assert v2.clinical_only(patient())["design_id"] == v2.clinical_only(patient())["design_id"]


def test_design_id_changes_when_the_design_changes():
    a = v2.clinical_only(patient())["design_id"]
    b = v2.clinical_only(patient(visual_stress_score=10, csf_band="Low"))["design_id"]
    assert a != b


def test_design_id_carries_no_optical_information():
    """The ID must not encode the recipe: two designs differing only slightly
    must not produce neighbouring IDs in any readable way."""
    did = v2.clinical_only(patient())["design_id"]
    design = v2.REGISTRY._get(did)
    for r in design["recipes"]:
        assert str(r.nso_peak_target_d) not in did
        assert str(int(r.mean_fill_factor_pct)) not in did


# --------------------------------------------------------------------------- #
# Manufacturing API
# --------------------------------------------------------------------------- #

def test_submit_returns_a_job_handle_not_a_recipe(client):
    did = client.post("/api/predict", json=quick_payload()).json()["design_id"]
    assert client.post("/api/design/approve", json={"design_id": did}).status_code == 200
    resp = client.post("/api/manufacturing/submit", json={"design_id": did})
    assert resp.status_code == 200
    _assert_clean(resp.text)
    job = resp.json()
    assert job["status"] == "Submitted"
    assert job["job_id"].startswith("NSO-")
    assert len(job["job_id"].split("-")) == 5


def test_submit_rejects_an_unknown_design(client):
    assert client.post(
        "/api/manufacturing/submit", json={"design_id": "NSO-P000Z"}
    ).status_code == 404


def test_submit_splits_the_work_across_vendors(client):
    did = client.post("/api/predict", json=quick_payload()).json()["design_id"]
    assert client.post("/api/design/approve", json={"design_id": did}).status_code == 200
    segments = client.post("/api/manufacturing/submit", json={"design_id": did}).json()["segments"]
    assert {s["segment"] for s in segments} == set(v2.SEGMENT_CODES)
    # The clinic-facing response must not describe what each segment IS.
    assert all(set(s) == {"segment", "package", "status"} for s in segments)


def test_package_endpoint_is_disabled_without_a_configured_key(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", {})
    did = v2.clinical_only(patient())["design_id"]
    resp = client.post(
        "/api/manufacturing/package",
        json={"design_id": did, "segment": "BS"},
    )
    assert resp.status_code == 503


def test_package_endpoint_rejects_a_wrong_key(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", _ALL_ROLE_KEYS)
    did = v2.clinical_only(patient())["design_id"]
    resp = client.post(
        "/api/manufacturing/package",
        json={"design_id": did, "segment": "BS"},
        headers={"x-api-key": "wrong"},
    )
    assert resp.status_code == 401


def test_each_vendor_receives_only_its_own_segment(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", _ALL_ROLE_KEYS)
    did = v2.clinical_only(patient())["design_id"]

    def pull(segment):
        return client.post(
            "/api/manufacturing/package",
            json={"design_id": did, "segment": segment},
            headers={"x-api-key": "secret"},
        ).json()

    front = pull("FS")
    back = pull("BS")
    assert front["format"] == "element_placement/v2"
    assert back["format"] == "sag_map/v1"
    # Each vendor gets its own geometry and only its own.
    assert front["eyes"]["OD"]["elements"]
    assert "points" not in front["eyes"]["OD"]
    assert back["eyes"]["OD"]["points"]
    assert "elements" not in back["eyes"]["OD"]


def test_no_vendor_segment_carries_the_patient_phenotype(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", _ALL_ROLE_KEYS)
    did = v2.clinical_only(patient())["design_id"]
    for segment in v2.SEGMENT_CODES:
        body = client.post(
            "/api/manufacturing/package",
            json={"design_id": did, "segment": segment},
            headers={"x-api-key": "secret"},
        ).text.lower()
        for term in ("phenotype", "myopia", "axial", "npc", "phoria", "\"age\""):
            assert term not in body, f"{segment} segment leaked '{term}'"


def test_unknown_segment_is_rejected(client, monkeypatch):
    """Authorization runs first, so an unknown segment is 403 rather than 400:
    the API does not confirm which segment codes exist."""
    monkeypatch.setattr(api, "VENDOR_KEYS", _ALL_ROLE_KEYS)
    did = v2.clinical_only(patient())["design_id"]
    resp = client.post(
        "/api/manufacturing/package",
        json={"design_id": did, "segment": "XX"},
        headers={"x-api-key": "secret"},
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Confidence, follow-up, tiering
# --------------------------------------------------------------------------- #

def test_confidence_rises_as_optional_domains_are_measured():
    quick = v2.clinical_only(patient())["prediction_confidence"]
    full = v2.clinical_only(patient(
        pfv=18, amplitude_of_accommodation=12, csf_low=70, csf_mid=68, csf_high=55,
        vep=1.0, hoa_rms=0.25,
    ))["prediction_confidence"]
    assert full > quick


def test_measured_domains_flags_match_the_supplied_data():
    m = v2.clinical_only(patient(pfv=18, hoa_rms=0.3))["measured_domains"]
    assert m["binocular_extended"] is True
    assert m["wavefront"] is True
    assert m["csf_measured"] is False


def test_clinical_followup_uses_support_levels_not_design_tiers():
    fu = v2.clinical_followup(24.50, 24.80, 12, "Level 1")
    _assert_clean(json.dumps(fu))
    assert fu["next_support_level"] in v2.SUPPORT_LEVELS.values()
    assert "Medium" not in json.dumps(fu)
    assert "High" not in json.dumps(fu)


@pytest.mark.parametrize("followup_al,band", [
    (24.55, "Controlled"), (24.65, "Borderline"), (24.90, "Progressing"),
])
def test_followup_progression_bands(followup_al, band):
    assert v2.clinical_followup(24.50, followup_al, 12)["progression_band"] == band


def test_followup_flags_a_required_refit():
    stable = v2.clinical_followup(24.50, 24.55, 12, "Level 2")
    progressing = v2.clinical_followup(24.50, 24.95, 12, "Level 2")
    assert stable["refit_required"] is False
    assert progressing["refit_required"] is True


def test_followup_endpoint(client):
    resp = client.post("/api/followup", json={
        "baseline_al": 24.5, "followup_al": 24.9, "interval_months": 12,
        "current_support_level": "Level 2",
    })
    assert resp.status_code == 200
    _assert_clean(resp.text)
    assert resp.json()["progression_band"] == "Progressing"


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #

def test_prediction_report_is_a_pdf_without_design_parameters(client):
    resp = client.post("/api/report/prediction", json=quick_payload())
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"
    body = resp.content.lower()
    # The PDF stream is compressed, so check the terms that survive as text
    # metadata plus the fact that the Design ID is present in the source data.
    for term in (b"fill factor", b"microstructure", b"jitter"):
        assert term not in body


def test_followup_report_is_a_pdf(client):
    body = quick_payload()
    body.update({"baseline_al": 24.5, "followup_al": 24.8, "interval_months": 12})
    resp = client.post("/api/report/followup", json=body)
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_health(client):
    assert client.get("/api/health").json()["version"] == "2.0"


# --------------------------------------------------------------------------- #
# Tiering: Quick Fitting must be enough on its own
# --------------------------------------------------------------------------- #

def test_quick_fitting_alone_produces_a_complete_result(client):
    resp = client.post("/api/predict", json=quick_payload())
    assert resp.status_code == 200
    r = resp.json()
    for key in ("design_id", "phenotype", "indices", "predicted",
                "explainable_summary", "manufacturing_status"):
        assert key in r


def test_quick_fitting_body_is_at_most_fifteen_fields():
    """Tier 1 is meant to stay around 10-15 inputs on screen."""
    assert len(quick_payload()) <= 15


# --------------------------------------------------------------------------- #
# Joint (binocular) optimization
# --------------------------------------------------------------------------- #

def test_pair_is_selected_over_the_full_cross_product():
    result = v2.run_fitting(patient())
    cfg = v2.get_config()
    per_eye = len(cfg.candidate_offsets) * len(cfg.candidate_density_offsets)
    assert result["clinical"]["joint_optimization"]["pairs_evaluated"] == per_eye ** 2


def test_joint_optimization_can_overrule_the_monocular_optimum():
    """An anisometropic patient is where the pair term must bite."""
    r = v2.run_fitting(patient(
        od={"sphere": -1.0, "axial_length": 23.4},
        os={"sphere": -7.0, "axial_length": 26.4}))["clinical"]
    assert r["joint_optimization"]["overruled_monocular_choice"] is True
    rows = r["candidate_comparison"]["OD"] + r["candidate_comparison"]["OS"]
    # At least one eye's selected candidate is not its monocular best.
    assert any(row["selected"] and not row["monocular_best"] for row in rows)


def test_symmetric_patient_keeps_the_monocular_optimum():
    r = v2.run_fitting(patient(
        od={"sphere": -3.0, "axial_length": 25.0},
        os={"sphere": -3.0, "axial_length": 25.0}))["clinical"]
    assert r["joint_optimization"]["overruled_monocular_choice"] is False


def test_binocular_penalty_is_zero_for_identical_designs():
    d = v2.run_fitting(patient())["design"]["recipes"][0]
    idx = v2.ai_derived_indices(patient())
    assert v2._binocular_penalty(d, d, idx) == 0.0


def test_binocular_penalty_grows_with_interocular_difference():
    """Measured on candidates rather than on a fitted pair: the fitted pair has
    already had the penalty applied to it, so it is the wrong thing to test
    the penalty with."""
    p = patient()
    idx = v2.ai_derived_indices(p)
    candidates = sorted(
        v2.generate_candidates(p, "OD", idx),
        key=lambda c: (c["recipe"].nso_peak_target_d,
                       c["recipe"].mean_fill_factor_pct),
    )
    gentlest = candidates[0]["recipe"]
    strongest = candidates[-1]["recipe"]

    assert (v2._binocular_penalty(gentlest, strongest, idx)
            > v2._binocular_penalty(gentlest, gentlest, idx))


def test_binocular_compatibility_falls_when_the_pair_costs_more():
    balanced = v2.clinical_only(patient())["predicted"]["binocular_compatibility"]
    split = v2.clinical_only(patient(
        od={"sphere": -1.0, "axial_length": 23.4},
        os={"sphere": -7.0, "axial_length": 26.4},
    ))["predicted"]["binocular_compatibility"]
    assert split < balanced


def test_joint_optimization_block_leaks_nothing():
    _assert_clean(json.dumps(v2.clinical_only(patient())["joint_optimization"]))


# --------------------------------------------------------------------------- #
# Section 4 auto-calculated descriptors + new Section 1/2 fields
# --------------------------------------------------------------------------- #

def test_csf_descriptors_are_none_without_a_measured_triplet():
    d = v2.csf_descriptors(patient())
    assert d == {"csf_auc": None, "csf_slope": None, "csf_centroid_cpd": None}


def test_csf_descriptors_computed_from_the_triplet():
    d = v2.csf_descriptors(patient(csf_low=90, csf_mid=70, csf_high=40))
    assert d["csf_auc"] is not None
    assert d["csf_slope"] < 0            # sensitivity falls with frequency
    assert 1.5 <= d["csf_centroid_cpd"] <= 18.0


def test_flat_csf_has_zero_slope_and_central_centroid():
    d = v2.csf_descriptors(patient(csf_low=70, csf_mid=70, csf_high=70))
    assert d["csf_slope"] == 0.0
    assert d["csf_centroid_cpd"] == pytest.approx(
        sum(v2.CSF_FREQUENCIES_CPD) / 3, abs=0.01)


def test_steeper_rolloff_gives_a_more_negative_slope():
    gentle = v2.csf_descriptors(patient(csf_low=80, csf_mid=70, csf_high=60))["csf_slope"]
    steep = v2.csf_descriptors(patient(csf_low=100, csf_mid=60, csf_high=20))["csf_slope"]
    assert steep < gentle


def test_descriptors_appear_in_the_clinical_payload():
    r = v2.clinical_only(patient(csf_low=90, csf_mid=70, csf_high=40))
    assert r["spatial_frequency_descriptors"]["csf_auc"] is not None
    _assert_clean(json.dumps(r["spatial_frequency_descriptors"]))


def test_interocular_acuity_difference_is_none_without_both_bcva():
    assert v2.interocular_acuity_difference(patient()) is None


def test_interocular_acuity_difference_computed():
    p = patient(od={"sphere": -3, "axial_length": 25.0, "bcva_logmar": 0.0},
                os={"sphere": -3, "axial_length": 25.0, "bcva_logmar": 0.2})
    assert v2.interocular_acuity_difference(p) == pytest.approx(0.2)


def test_binocular_balance_grade_lowers_image_balance_index():
    normal = v2.interocular_image_balance_index(patient(binocular_balance="Normal"))
    mild = v2.interocular_image_balance_index(patient(binocular_balance="Mild"))
    significant = v2.interocular_image_balance_index(patient(binocular_balance="Significant"))
    assert normal > mild > significant


# --------------------------------------------------------------------------- #
# Manufacturing projection — geometry and parameter abstraction
# --------------------------------------------------------------------------- #

# Design-language terms a vendor must never be able to read off a package.
DESIGN_LANGUAGE = [
    "spherical", "aberration", "sa_", "fill", "density", "jitter", "zone",
    "mtf", "sphere", "cylinder", "axis", "target", "profile", "entropy",
    "temporal", "blue-noise", "phenotype",
]


def _recipe(eye="OD"):
    design = v2.run_fitting(patient())["design"]
    return next(r for r in design["recipes"] if r.eye == eye)


def test_surface_map_is_a_coordinate_grid():
    sm = v2.surface_map(_recipe())
    assert sm["sample_count"] == v2.SURFACE_RADIAL_SAMPLES * v2.SURFACE_MERIDIONAL_SAMPLES
    assert len(sm["points"]) == sm["sample_count"]
    assert all(len(pt) == 3 for pt in sm["points"])


def test_surface_sag_is_physically_plausible():
    """Sag over a 65 mm blank is a few millimetres from the prescription
    alone; what must not happen is the higher-order term adding millimetres
    on top of that."""
    zs = [pt[2] for pt in v2.surface_map(_recipe())["points"]]
    assert max(abs(z) for z in zs) < 6.0


def test_surface_sag_is_zero_at_the_centre():
    sm = v2.surface_map(_recipe())
    centre = [pt for pt in sm["points"] if pt[0] == 0.0]
    assert all(pt[2] == 0.0 for pt in centre)


def test_the_microstructure_does_not_reach_the_sag_map():
    """The two channels are independent. Changing what the microstructure asks
    for must not move the back surface -- folding the NSO modulation into the
    sag as a spherical-aberration term is precisely the error the channel split
    exists to prevent."""
    weak = v2.run_fitting(patient(visual_stress_score=10))["design"]["recipes"][0]
    strong = v2.run_fitting(patient(visual_stress_score=0))["design"]["recipes"][0]
    assert (weak.nso_modulation.zone("A").element_height_um
            != strong.nso_modulation.zone("A").element_height_um)
    assert v2.surface_map(weak)["points"] == v2.surface_map(strong)["points"]


def test_microstructure_map_is_paginated():
    r = _recipe()
    page0 = v2.microstructure_map(r, page=0)
    assert page0["estimated_total_elements"] > page0["returned"]
    assert page0["total_pages"] > 1
    assert page0["next_page"] == 1
    page1 = v2.microstructure_map(r, page=1)
    assert page1["elements"] != page0["elements"]


def test_microstructure_elements_stay_inside_their_zone_annulus():
    """Each zone is an annulus with its own element size and fill factor."""
    recipe = _recipe()
    page = v2.microstructure_map(recipe, page=0)
    zone = recipe.nso_modulation.zone(page["zone"])
    for x, y, _d, _l, _h in page["elements"]:
        r2 = x * x + y * y
        assert r2 <= zone.outer_radius_mm ** 2 + 1e-6
        assert r2 >= zone.inner_radius_mm ** 2 - 1e-6


def test_microstructure_map_is_reproducible_for_a_design():
    r = _recipe()
    assert v2.microstructure_map(r)["elements"] == v2.microstructure_map(r)["elements"]


def test_microstructure_elements_are_jittered_not_on_a_grid():
    xs = [e[0] for e in v2.microstructure_map(_recipe())["elements"]]
    assert len(set(xs)) > len(xs) // 2   # a plain grid would repeat x values


@pytest.mark.parametrize("segment", ["FS", "BS", "AV"])
def test_no_package_carries_design_language(client, monkeypatch, segment):
    monkeypatch.setattr(api, "VENDOR_KEYS", _ALL_ROLE_KEYS)
    did = v2.clinical_only(patient())["design_id"]
    body = client.post(
        "/api/manufacturing/package",
        json={"design_id": did, "segment": segment},
        headers={"x-api-key": "secret"},
    ).text.lower()
    for term in DESIGN_LANGUAGE:
        assert term not in body, f"{segment} package leaked design language '{term}'"


def test_verification_package_has_limits_but_not_targets(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", _ALL_ROLE_KEYS)
    did = v2.clinical_only(patient())["design_id"]
    av = client.post(
        "/api/manufacturing/package",
        json={"design_id": did, "segment": "AV"},
        headers={"x-api-key": "secret"},
    ).json()
    od = av["eyes"]["OD"]
    assert all(k.endswith("_tolerance_mm") for k in od)


def test_package_pagination_is_reachable_over_the_api(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", _ALL_ROLE_KEYS)
    did = v2.clinical_only(patient())["design_id"]
    r = client.post(
        "/api/manufacturing/package",
        json={"design_id": did, "segment": "FS", "page": 2},
        headers={"x-api-key": "secret"},
    ).json()
    assert r["eyes"]["OD"]["page"] == 2


# --------------------------------------------------------------------------- #
# Optical verification
# --------------------------------------------------------------------------- #

def test_verification_passes_inside_the_window():
    did = v2.clinical_only(patient())["design_id"]
    r = v2.optical_verification(did, {"sag_error_mm": 0.001, "decentration_mm": 0.1})
    assert r["verdict"] == "Pass"


def test_verification_fails_outside_the_window():
    did = v2.clinical_only(patient())["design_id"]
    r = v2.optical_verification(did, {"sag_error_mm": 0.5})
    assert r["verdict"] == "Fail"
    assert r["checks"][0]["pass"] is False


def test_verification_without_measurements_is_incomplete():
    did = v2.clinical_only(patient())["design_id"]
    assert v2.optical_verification(did, {})["verdict"] == "Incomplete"


def test_verification_sign_is_ignored():
    did = v2.clinical_only(patient())["design_id"]
    assert v2.optical_verification(did, {"sag_error_mm": -0.001})["verdict"] == "Pass"


def test_verification_endpoint_requires_the_vendor_key(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", _ALL_ROLE_KEYS)
    did = v2.clinical_only(patient())["design_id"]
    assert client.post("/api/manufacturing/verify",
                       json={"design_id": did, "sag_error_mm": 0.001},
                       headers={"x-api-key": "nope"}).status_code == 401
    ok = client.post("/api/manufacturing/verify",
                     json={"design_id": did, "sag_error_mm": 0.001},
                     headers={"x-api-key": "secret"})
    assert ok.status_code == 200
    _assert_clean(ok.text)


# --------------------------------------------------------------------------- #
# Closed loop — clinical feedback -> AI refitting
# --------------------------------------------------------------------------- #

def test_refit_produces_a_new_linked_design():
    p = patient()
    old = v2.clinical_only(p)["design_id"]
    new = v2.refit(p, old, 25.1, 25.55, 12)
    assert new["design_id"] != old
    assert new["refit"]["previous_design_id"] == old
    assert new["refit"]["revision"] == 1
    assert new["design_id"] == old + "-R1"


def test_refit_escalates_the_design_when_progressing():
    p = patient()
    old = v2.clinical_only(p)["design_id"]
    before = v2.REGISTRY._get(old)["recipes"][0].nso_modulation.zone("A").element_height_um
    new = v2.refit(p, old, 25.1, 25.55, 12)
    after = v2.REGISTRY._get(new["design_id"])["recipes"][0].nso_modulation.zone("A").element_height_um
    assert new["refit"]["progression_band"] == "Progressing"
    assert after > before


def test_refit_does_not_escalate_a_controlled_eye():
    p = patient()
    old = v2.clinical_only(p)["design_id"]
    new = v2.refit(p, old, 25.1, 25.13, 12)
    assert new["refit"]["progression_band"] == "Controlled"
    assert new["refit"]["escalation_applied"] is False


def test_refit_payload_leaks_nothing():
    p = patient()
    old = v2.clinical_only(p)["design_id"]
    _assert_clean(json.dumps(v2.refit(p, old, 25.1, 25.55, 12)))


def test_refit_rejects_an_unknown_previous_design(client):
    body = quick_payload()
    body.update({"previous_design_id": "NSO-P000Z", "baseline_al": 25.1,
                 "followup_al": 25.5, "interval_months": 12})
    assert client.post("/api/refit", json=body).status_code == 404


def test_refit_endpoint_round_trip(client):
    did = client.post("/api/predict", json=quick_payload()).json()["design_id"]
    body = quick_payload()
    body.update({"previous_design_id": did, "baseline_al": 25.1,
                 "followup_al": 25.55, "interval_months": 12})
    resp = client.post("/api/refit", json=body)
    assert resp.status_code == 200
    _assert_clean(resp.text)
    assert resp.json()["refit"]["design_changed"] is True


def test_client_cannot_set_the_closed_loop_escalation(client):
    """progression_load is server state, not a client-supplied input."""
    baseline = client.post("/api/predict", json=quick_payload()).json()
    injected = client.post(
        "/api/predict", json=quick_payload(progression_load=0.8)).json()
    assert injected["design_id"] == baseline["design_id"]


def test_followup_advice_drops_the_design_ladder_vocabulary():
    """nso_core says 'NSO strength'; the clinical layer must not."""
    for al in (24.55, 24.65, 24.90):
        advice = v2.clinical_followup(24.50, al, 12)["advice"]
        assert "NSO" not in advice
        assert "profile" not in advice.lower()
