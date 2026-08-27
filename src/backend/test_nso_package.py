"""
Tests for the package architecture: the config seam, the ML seam, the predictor
interface, per-vendor authorization, and the layering rules that keep the IP
boundary enforced by imports and not only by the guard.
"""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api
import nso
from nso.config import EngineConfig, use_config


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture
def restore_config():
    """Any test that swaps the config must put the original back."""
    original = nso.get_config()
    yield
    use_config(original)


def patient(**over):
    od = nso.EyeInput(**over.pop("od", {"sphere": -3.25, "axial_length": 25.1}))
    os_ = nso.EyeInput(**over.pop("os", {"sphere": -3.0, "axial_length": 24.9}))
    base = dict(age=11, photopic_pupil=5.2, visual_stress_score=6)
    base.update(over)
    return nso.PatientInput(od=od, os=os_, **base)


# --------------------------------------------------------------------------- #
# Config seam — calibration must be a config change, not a code change
# --------------------------------------------------------------------------- #

def test_config_round_trips_through_json():
    cfg = nso.get_config()
    assert EngineConfig.from_dict(json.loads(cfg.to_json())) == cfg


def test_config_can_be_loaded_from_a_file(tmp_path: Path):
    path = tmp_path / "calibrated.json"
    path.write_text(nso.get_config().evolve(version="calib-1").to_json())
    assert EngineConfig.load(path).version == "calib-1"


def test_config_rejects_unknown_fields():
    with pytest.raises(ValueError, match="unknown config fields"):
        EngineConfig.from_dict({"not_a_real_parameter": 1})


def test_config_is_immutable():
    with pytest.raises(Exception):
        nso.get_config().lens_index = 1.74


def test_changing_a_coefficient_changes_the_design(restore_config):
    """The point of the config seam: recalibrating touches no engine code."""
    def height():
        r = nso.run_fitting(patient())["design"]["recipes"][0]
        return r.nso_modulation.zone("A").element_height_um

    before = height()
    # Recalibrating the reference geometry is the realistic case: new bench
    # measurements arrive and the table is updated.
    recalibrated = dict(nso.get_config().zone_reference_geometry)
    recalibrated["A"] = {**recalibrated["A"], "height_um": 3.0}
    use_config(nso.get_config().evolve(
        zone_reference_geometry=recalibrated, version="recalibrated"))
    assert height() != before


def test_changing_grading_thresholds_changes_the_phenotype(restore_config):
    before = nso.clinical_only(patient())["phenotype"]["code"]
    use_config(nso.get_config().evolve(grade_low_ceiling=5.0, grade_mid_ceiling=10.0))
    after = nso.clinical_only(patient())["phenotype"]["code"]
    assert after != before


def test_geometry_follows_the_configured_lens(restore_config):
    r = nso.run_fitting(patient())["design"]["recipes"][0]
    before = nso.surface_map(r)["optic_diameter_mm"]
    use_config(nso.get_config().evolve(optic_zone_diameter_mm=50.0))
    assert nso.surface_map(r)["optic_diameter_mm"] == 50.0 != before


def test_config_version_is_reported_with_every_result(restore_config):
    use_config(nso.get_config().evolve(version="calib-9"))
    assert nso.clinical_only(patient())["engine"]["config_version"] == "calib-9"


# --------------------------------------------------------------------------- #
# ML seam — feature vectors
# --------------------------------------------------------------------------- #

def test_patient_and_design_vectors_are_disjoint():
    p = patient()
    r = nso.run_fitting(p)["design"]["recipes"][0]
    pf, df = nso.patient_features(p), nso.design_features(r)
    assert set(pf.values) & set(df.values) == set()


def test_feature_order_is_stable_across_patients():
    a = nso.patient_features(patient())
    b = nso.patient_features(patient(age=17, visual_stress_score=1))
    assert a.names() == b.names()
    assert len(a.to_array()) == len(b.to_array())


def test_every_feature_is_numeric():
    p = patient()
    r = nso.run_fitting(p)["design"]["recipes"][0]
    for fv in (nso.patient_features(p), nso.design_features(r)):
        assert all(isinstance(v, float) for v in fv.to_array())


def test_missing_measurements_are_flagged_not_silently_imputed():
    """A model must be able to tell 'average' from 'not measured'."""
    absent = nso.patient_features(patient()).values
    present = nso.patient_features(patient(pfv=18)).values
    assert absent["pfv_measured"] == 0.0
    assert present["pfv_measured"] == 1.0
    assert present["pfv"] == 18.0


def test_every_optional_feature_has_a_measured_indicator():
    values = nso.patient_features(patient()).values
    indicators = {k[: -len("_measured")] for k in values if k.endswith("_measured")}
    for name in indicators:
        assert name in values, f"{name}_measured has no matching value"


def test_patient_vector_carries_no_design_information():
    nso.assert_no_design_leak(nso.patient_features(patient()).values)


def test_design_vector_is_design_ip():
    """Sanity check in the other direction: the guard must reject it."""
    r = nso.run_fitting(patient())["design"]["recipes"][0]
    with pytest.raises(nso.DesignLeakError):
        nso.assert_no_design_leak(nso.design_features(r).values)


def test_training_row_joins_both_vectors_and_an_outcome():
    p = patient()
    r = nso.run_fitting(p)["design"]["recipes"][0]
    row = nso.training_row(
        nso.patient_features(p), nso.design_features(r),
        outcome={"annualized_delta_al": 0.12, "dropped_out": 0},
        design_id="NSO-P000A",
    )
    assert row["schema_version"] == nso.FeatureVector.SCHEMA_VERSION
    assert row["design_id"] == "NSO-P000A"
    assert row["outcome__annualized_delta_al"] == 0.12
    assert any(k.startswith("patient__") for k in row)
    assert any(k.startswith("design__") for k in row)


def test_training_row_is_flat_and_serializable():
    p = patient()
    r = nso.run_fitting(p)["design"]["recipes"][0]
    row = nso.training_row(nso.patient_features(p), nso.design_features(r))
    assert all(not isinstance(v, (dict, list)) for v in row.values())
    json.dumps(row)


# --------------------------------------------------------------------------- #
# Predictor interface — the swap point for a trained model
# --------------------------------------------------------------------------- #

class StubPredictor:
    """A stand-in for a future learned model."""

    name = "stub"
    version = "0.1.0"
    schema_version = nso.FeatureVector.SCHEMA_VERSION

    def predict(self, patient_fv, design_fv):
        nso.predictors.check_schema(self, patient_fv, design_fv)
        return nso.OutcomePrediction(
            control=90.0, comfort=90.0, adaptation=90.0, acuity=90.0,
            manufacturability=90.0,
            predictor_name=self.name, predictor_version=self.version,
            schema_version=patient_fv.SCHEMA_VERSION,
            config_version=nso.get_config().version,
            uncertainty={"control": 3.2},
        )


@pytest.fixture
def stub_predictor():
    nso.register(StubPredictor())
    previous = nso.use_predictor("stub")
    yield
    nso.use_predictor(previous)


def test_rule_based_predictor_satisfies_the_protocol():
    assert isinstance(nso.get_predictor(), nso.Predictor)


def test_a_new_predictor_can_be_swapped_in_without_touching_the_engine(stub_predictor):
    r = nso.clinical_only(patient())
    assert r["engine"]["predictor"] == "stub"
    assert r["predicted"]["nso_control_score"] == 90


def test_swapping_the_predictor_does_not_change_which_designs_exist(stub_predictor):
    """A model forecasts outcomes; it must not widen the design space."""
    cfg = nso.get_config()
    assert len(nso.clinical_only(patient())["candidate_comparison"]["OD"]) == (
        len(cfg.candidate_offsets) * len(cfg.candidate_density_offsets)
    )


def test_predictions_are_stamped_with_their_provenance():
    engine = nso.clinical_only(patient())["engine"]
    assert engine["predictor"] == "rule_based"
    assert set(engine) == {
        "predictor", "predictor_version", "feature_schema", "config_version"
    }


def test_schema_mismatch_is_an_error_not_silent_nonsense():
    class StalePredictor(StubPredictor):
        name, schema_version = "stale", "0.0.1"

    p = patient()
    r = nso.run_fitting(p)["design"]["recipes"][0]
    with pytest.raises(nso.SchemaMismatchError):
        StalePredictor().predict(nso.patient_features(p), nso.design_features(r))


def test_unknown_predictor_name_is_rejected():
    with pytest.raises(KeyError):
        nso.use_predictor("does_not_exist")


def test_predictor_provenance_reaches_the_api(client):
    body = client.get("/api/health").json()
    assert body["engine"] == "rule_based"
    assert body["feature_schema"] == nso.FeatureVector.SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# Per-vendor authorization (ASSUMPTIONS P2-3)
# --------------------------------------------------------------------------- #

ROLE_KEYS = {
    "fs-key": "front_surface",
    "bs-key": "back_surface",
    "av-key": "assembly",
}


def test_a_vendor_key_opens_only_its_own_segment(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", ROLE_KEYS)
    did = nso.clinical_only(patient())["design_id"]

    def pull(key, segment):
        return client.post(
            "/api/manufacturing/package",
            json={"design_id": did, "segment": segment},
            headers={"x-api-key": key},
        ).status_code

    assert pull("fs-key", "FS") == 200
    assert pull("fs-key", "BS") == 403
    assert pull("fs-key", "AV") == 403
    assert pull("bs-key", "BS") == 200
    assert pull("bs-key", "FS") == 403


def test_no_single_external_key_can_assemble_the_whole_design(client, monkeypatch):
    """The property segmentation exists to provide."""
    monkeypatch.setattr(api, "VENDOR_KEYS", ROLE_KEYS)
    did = nso.clinical_only(patient())["design_id"]
    for key in ROLE_KEYS:
        granted = [
            seg for seg in nso.SEGMENT_CODES
            if client.post(
                "/api/manufacturing/package",
                json={"design_id": did, "segment": seg},
                headers={"x-api-key": key},
            ).status_code == 200
        ]
        assert len(granted) == 1, f"{key} reached {granted}"


def test_verification_requires_the_assembly_role(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", ROLE_KEYS)
    did = nso.clinical_only(patient())["design_id"]
    body = {"design_id": did, "sag_error_mm": 0.001}
    assert client.post("/api/manufacturing/verify", json=body,
                       headers={"x-api-key": "fs-key"}).status_code == 403
    assert client.post("/api/manufacturing/verify", json=body,
                       headers={"x-api-key": "av-key"}).status_code == 200


# --------------------------------------------------------------------------- #
# Pagination (ASSUMPTIONS P2-5, P2-6)
# --------------------------------------------------------------------------- #

def _recipe():
    return nso.run_fitting(patient())["design"]["recipes"][0]


def test_any_page_is_reachable_without_walking_earlier_pages():
    r = _recipe()
    total = nso.microstructure_map(r, page=0)["total_pages"]

    # A page in the middle of the zone, fetched cold.
    middle = nso.microstructure_map(r, page=total // 2)
    assert middle["returned"] > 0
    assert middle["next_page"] == total // 2 + 1

    # The last page terminates the walk. It may legitimately be empty: the
    # sampling grid is square and the optic zone is circular, so the outermost
    # rows can fall entirely outside it.
    last = nso.microstructure_map(r, page=total - 1)
    assert last["next_page"] is None


def test_pages_are_reproducible():
    r = _recipe()
    assert nso.microstructure_map(r, page=7) == nso.microstructure_map(r, page=7)


def test_pages_do_not_overlap():
    r = _recipe()
    a = {tuple(e[:2]) for e in nso.microstructure_map(r, page=3)["elements"]}
    b = {tuple(e[:2]) for e in nso.microstructure_map(r, page=4)["elements"]}
    assert a and b and not (a & b)


def test_pagination_cost_does_not_grow_with_page_number():
    """The property that matters: a late page must cost the same as an early
    one. Total walk time scales with the lens area and is not the thing to
    assert -- a 65 mm blank simply has more elements than a 40 mm one. What
    would be a defect is page N costing N times page 1, which is what happens
    when pagination is implemented by walking from the start each time."""
    import time
    r = _recipe()
    total_pages = nso.microstructure_map(r, page=0)["total_pages"]
    assert total_pages > 10, "need enough pages for this to mean anything"

    def timed(page):
        start = time.time()
        nso.microstructure_map(r, page=page)
        return time.time() - start

    early = timed(1)
    late = timed(total_pages - 2)
    assert late < early * 4 + 0.5, (
        f"page {total_pages - 2} took {late:.2f}s vs {early:.2f}s for page 1 — "
        "pagination looks quadratic again"
    )


def test_element_total_is_labelled_an_estimate():
    """It is a density-times-area estimate, not an exact count -- the field
    name must say so (ASSUMPTIONS P2-6)."""
    page = nso.microstructure_map(_recipe(), page=0)
    assert "estimated_total_elements" in page
    assert "total_elements" not in page


# --------------------------------------------------------------------------- #
# Persistence seam (ASSUMPTIONS P2-1)
# --------------------------------------------------------------------------- #

def test_registry_accepts_an_alternative_store():
    """Swapping in a database means implementing DesignStore, nothing more."""
    calls = []

    class RecordingStore(nso.InMemoryDesignStore):
        def put(self, design_id, payload):
            calls.append(design_id)
            super().put(design_id, payload)

    registry = nso.DesignRegistry(store=RecordingStore())
    registry.register("NSO-P123A", {"recipes": [], "revision": 1})
    assert calls == ["NSO-P123A"]
    assert registry.known("NSO-P123A")


def test_in_memory_store_satisfies_the_protocol():
    assert isinstance(nso.InMemoryDesignStore(), nso.DesignStore)


# --------------------------------------------------------------------------- #
# IP guard hardening (ASSUMPTIONS P2-11)
# --------------------------------------------------------------------------- #

def test_guard_catches_design_language_in_a_free_text_value():
    with pytest.raises(nso.DesignLeakError):
        nso.assert_no_design_leak({"note": "Uses spherical aberration of 3.76 D"})


def test_guard_catches_design_language_inside_a_list_of_strings():
    with pytest.raises(nso.DesignLeakError):
        nso.assert_no_design_leak({"summary": ["fine", "blue-noise fill factor 35%"]})


def test_guard_allows_ordinary_clinical_prose():
    nso.assert_no_design_leak(
        {"summary": ["High refractive risk favours a stronger configuration."]}
    )


# --------------------------------------------------------------------------- #
# Layering — the IP boundary should hold by construction
# --------------------------------------------------------------------------- #

def test_lower_layers_do_not_import_the_clinical_layer():
    """Import direction is one-way; a cycle here would let design details
    creep back into the payload assembler unnoticed."""
    root = Path(nso.__file__).parent
    for module in ("config.py", "ip.py", "patient.py", "recipe.py", "features.py"):
        source = (root / module).read_text()
        assert "from .clinical" not in source
        assert "import clinical" not in source


def test_config_module_has_no_engine_dependencies():
    source = (Path(nso.__file__).parent / "config.py").read_text()
    assert "import nso_core" not in source
    assert "from ." not in source


def test_recipe_module_is_dependency_free():
    """DesignRecipe must stay swappable for a database row."""
    source = (Path(nso.__file__).parent / "recipe.py").read_text()
    assert "from ." not in source


# --------------------------------------------------------------------------- #
# Live inputs — a clinical field the clinician can set must change something.
#
# A dropdown or a measurement that silently changes nothing is worse than a
# crash: it creates confidence in a decision the engine never made. These tests
# exist because "Primary Optimization Goal" shipped inert.
# --------------------------------------------------------------------------- #

def _fingerprint(p):
    r = nso.run_fitting(p)
    c = r["clinical"]
    return (
        c["design_id"],
        c["phenotype"]["code"],
        c["prediction_confidence"],
        tuple(sorted(c["predicted"].items())),
        tuple(sorted(c["indices"].items())),
        tuple(sorted(c["candidate_comparison"]["OD"][0].items())),
        tuple((x.nso_peak_target_d, x.mean_fill_factor_pct) for x in r["design"]["recipes"]),
    )


def _probe_patient():
    return nso.PatientInput(
        # A CSF triplet is present so the protocol fields have something to
        # apply to -- frequencies and scale change how a curve is read, and
        # with no curve there is nothing to read.
        csf_low=90.0, csf_mid=70.0, csf_high=40.0,
        age=11,
        od=nso.EyeInput(sphere=-3.25, cylinder=-0.5, axis=180,
                        axial_length=25.1, bcva_logmar=0.0),
        os=nso.EyeInput(sphere=-3.0, cylinder=-0.25, axis=175,
                        axial_length=24.9, bcva_logmar=0.1),
        photopic_pupil=5.2, near_phoria=-4, npc=9, accommodative_lag=1.1,
        visual_stress_score=6, near_hours=7, digital_hours=5, outdoor_hours=0.8,
    )


# Fields that are still inert, with what each one needs before it can be wired.
# Shrink this list; never grow it. A new field added without an effect fails
# ``test_no_new_inert_clinical_inputs`` instead of shipping quietly.
KNOWN_INERT = {
    "ocular_dominance": "needs a clinical rule for how dominance biases OD/OS",
    # Provenance, not inputs. These are stored so a stored result can be traced
    # to the instrument that produced it; they are deliberately not arithmetic.
    "csf_device": "provenance — recorded with the measurement, never computed with",
    "csf_test_protocol": "provenance — recorded with the measurement",
    # Deliberately inert per the V2.1 baseline: a bare number with no stimulus,
    # unit or normative reference is a record, not evidence. The structured
    # forms (vep_z_score, fixation_stability, ...) are live and probed below.
    "vep": "bare value — use vep_z_score; recorded for the future dataset",
    "erg": "bare value — use erg_z_score; recorded for the future dataset",
    "eye_tracking": "bare value — use the named sub-metrics",
    "vep_stimulus": "provenance — recorded with the measurement",
    "erg_protocol": "provenance — recorded with the measurement",
    "vep_amplitude_uv": "recorded for the future dataset; not yet an input",
    "vep_latency_ms": "recorded for the future dataset; not yet an input",
    "vep_interocular_difference_ms": "recorded for the future dataset",
    "pupil_dynamics": "recorded for the future dataset; not yet an input",
    "gaze_distribution": "recorded for the future dataset; not yet an input",
    "distance_phoria": "binocular_load currently reads near phoria only",
    "near_working_distance": "accommodative demand is not derived from distance yet",
    "computer_working_distance": "same as near_working_distance",
    "corneal_astigmatism": "needs an optical rule from the supervisor",
    "corneal_eccentricity": "needs an optical rule from the supervisor",
}

# Plausible alternative values, one per clinical field.
INPUT_PROBES = {
    "primary_goal": "Night Vision",
    "ocular_dominance": "OD",
    "binocular_balance": "Significant",
    "distance_phoria": -6.0,
    "nfv": 8.0,
    "pfv": 10.0,
    "ac_a": 9.0,
    "stereoacuity": 200.0,
    "amplitude_of_accommodation": 6.0,
    "accommodative_facility": 4.0,
    "near_working_distance": 22.0,
    "computer_working_distance": 70.0,
    "typical_working_distance": 28.0,
    "mesopic_pupil": 7.5,
    "visual_comfort_score": 2.0,
    "neural_adaptation_score": 2.0,
    "dynamic_visual_stability": 2.0,
    "night_driving": True,
    "low_light_demand": "High",
    "vep": 1.5,
    "erg": 1.5,
    "eye_tracking": 1.5,
    "hoa_rms": 0.45,
    "corneal_sa": 0.30,
    "coma": 0.25,
    "trefoil": 0.2,
    "corneal_astigmatism": 1.5,
    "corneal_eccentricity": 0.7,
    "csf_device": "CSV-1000",
    "csf_test_protocol": "photopic",
    "csf_scale": "log_cs",
    "csf_frequencies_cpd": [1.0, 4.0, 16.0],
    "vep_stimulus": "pattern-reversal 1 deg",
    "vep_amplitude_uv": 8.4,
    "vep_latency_ms": 104.0,
    "vep_interocular_difference_ms": 3.0,
    "vep_z_score": -1.2,
    "erg_protocol": "ISCEV standard",
    "erg_z_score": -0.8,
    "fixation_stability": 12.0,
    "blink_rate": 14.0,
    "vergence_stability": 6.0,
    "pupil_dynamics": 6.0,
    "gaze_distribution": 6.0,
    "csf_measurement": nso.CsfMeasurement(
        spatial_frequency_cpd=[1.5, 3.0, 6.0, 12.0, 18.0],
        log_cs=[1.75, 1.90, 1.80, 1.40, 0.90],
        device="CSV-1000",
    ),
}


@pytest.mark.parametrize(
    "field", [f for f in INPUT_PROBES if f not in KNOWN_INERT]
)
def test_clinical_input_changes_the_result(field):
    from dataclasses import replace
    base = _probe_patient()
    altered = replace(base, **{field: INPUT_PROBES[field]})
    assert _fingerprint(altered) != _fingerprint(base), (
        f"'{field}' is inert: a clinician can set it and nothing changes"
    )


def test_no_new_inert_clinical_inputs():
    """Every settable clinical field is either live or on the known list."""
    settable = {
        f for f in nso.PatientInput.__dataclass_fields__
        # od/os are nested objects; progression_load is server-side state.
        if f not in ("od", "os", "progression_load")
    }
    untested = settable - set(INPUT_PROBES) - {
        "age", "photopic_pupil", "near_phoria", "npc", "accommodative_lag",
        "csf_band", "csf_low", "csf_mid", "csf_high", "visual_stress_score",
        "near_hours", "digital_hours", "outdoor_hours",
    }
    assert not untested, (
        f"clinical fields with no influence probe: {sorted(untested)} — add them "
        "to INPUT_PROBES so an inert field cannot ship unnoticed"
    )


# --------------------------------------------------------------------------- #
# Primary optimization goal
# --------------------------------------------------------------------------- #

def test_every_goal_has_weights_that_sum_to_one():
    for goal, weights in nso.get_config().goal_loss_weights.items():
        assert abs(sum(weights.values()) - 1.0) < 1e-9, goal


def test_every_offered_goal_has_a_weight_profile():
    configured = set(nso.get_config().goal_loss_weights)
    assert set(nso.PRIMARY_GOALS) == configured


def test_goal_weights_have_the_same_terms_as_the_default():
    default = set(nso.get_config().loss_weights)
    for goal, weights in nso.get_config().goal_loss_weights.items():
        assert set(weights) == default, goal


def test_an_unknown_goal_falls_back_instead_of_failing():
    cfg = nso.get_config()
    assert cfg.weights_for_goal("Not A Real Goal") == cfg.loss_weights


def test_myopia_and_comfort_goals_choose_different_designs():
    def fit(goal):
        p = _probe_patient()
        p.primary_goal = goal
        return nso.run_fitting(p)

    control_led = fit("Myopia Management")
    comfort_led = fit("Digital Visual Comfort")
    assert control_led["clinical"]["design_id"] != comfort_led["clinical"]["design_id"]
    # The control-led goal must actually buy control, and pay for it.
    assert (control_led["clinical"]["predicted"]["nso_control_score"]
            > comfort_led["clinical"]["predicted"]["nso_control_score"])
    assert (control_led["clinical"]["predicted"]["visual_comfort"]
            < comfort_led["clinical"]["predicted"]["visual_comfort"])


# --------------------------------------------------------------------------- #
# The candidate set must be a real trade-off, not a dominated ranking.
#
# When control and acuity did not depend on the design's SA, one candidate
# dominated on every axis and no weighting could ever pick another. The
# comparison table looked like a choice and was not.
# --------------------------------------------------------------------------- #

def _candidates():
    p = _probe_patient()
    return sorted(
        nso.generate_candidates(p, "OD", nso.ai_derived_indices(p)),
        key=lambda c: (c["recipe"].nso_peak_target_d, c["recipe"].mean_fill_factor_pct),
    )


@pytest.mark.parametrize(
    "term", ["control", "acuity", "comfort", "adaptation", "robustness",
             "manufacturability"]
)
def test_no_loss_term_is_constant_across_candidates(term):
    """An inert loss term makes its weight meaningless."""
    values = {c["metrics"][term] for c in _candidates()}
    if term == "manufacturability":
        pytest.skip("varies only once element height crosses its threshold")
    assert len(values) > 1, f"'{term}' is identical for every candidate"


def test_no_candidate_dominates_on_every_axis():
    axes = ["control", "acuity", "comfort", "adaptation"]
    cands = _candidates()
    for a in cands:
        others = [c for c in cands if c is not a]
        assert not all(
            all(a["metrics"][k] >= b["metrics"][k] for k in axes) for b in others
        ), f"candidate SA={a['recipe'].nso_peak_target_d} dominates; the choice is fake"


def test_more_optical_load_buys_control_and_costs_acuity():
    """The premise of the technology, stated as a test."""
    cands = _candidates()
    weak, strong = cands[0], cands[-1]
    assert strong["metrics"]["control"] > weak["metrics"]["control"]
    assert strong["metrics"]["acuity"] < weak["metrics"]["acuity"]
    assert strong["metrics"]["comfort"] < weak["metrics"]["comfort"]


def test_the_two_candidate_axes_trade_differently():
    """Strength and coverage must not be two names for the same knob.

    Both buy control, but only a higher optical target costs acuity. That
    asymmetry is what lets an acuity-led goal reach for coverage instead of
    strength; if the two axes traded identically the grid would be
    one-dimensional again.
    """
    candidates = _candidates()

    def group(target_d):
        rows = [c for c in candidates if c["recipe"].nso_peak_target_d == target_d]
        return sorted(rows, key=lambda c: c["recipe"].mean_fill_factor_pct)

    targets = sorted({c["recipe"].nso_peak_target_d for c in candidates})
    assert len(targets) >= 2, "the strength axis is not varying"

    # Coverage axis, at a fixed optical target: control up, acuity unchanged.
    same_target = group(targets[0])
    assert len(same_target) >= 2, "the coverage axis is not varying"
    low_cov, high_cov = same_target[0]["metrics"], same_target[-1]["metrics"]
    assert high_cov["control"] > low_cov["control"]
    assert high_cov["acuity"] == low_cov["acuity"]

    # Strength axis, at comparable coverage: control up, acuity down.
    weak = group(targets[0])[0]["metrics"]
    strong = group(targets[-1])[0]["metrics"]
    assert strong["control"] > weak["control"]
    assert strong["acuity"] < weak["acuity"]


def test_robustness_falls_as_the_surface_gets_busier():
    cands = _candidates()
    assert cands[-1]["metrics"]["robustness"] < cands[0]["metrics"]["robustness"]


# --------------------------------------------------------------------------- #
# Persistence (ASSUMPTIONS P2-1, P2-2)
#
# Losing the design store is not a cache miss: the clinician cannot submit, the
# vendor cannot pull, the follow-up cannot refit. These tests use a real file so
# they exercise the same path production takes.
# --------------------------------------------------------------------------- #

import subprocess
import sys
import textwrap

from nso.manufacturing.store import (
    InMemoryDesignStore,
    SQLiteDesignStore,
    dumps_entry,
    loads_entry,
    store_from_url,
)


@pytest.fixture
def sqlite_registry(tmp_path):
    return nso.DesignRegistry(store=SQLiteDesignStore(tmp_path / "nso.db"))


def _entry():
    p = patient()
    return nso.run_fitting(p)["design"]


def test_design_survives_a_new_registry_on_the_same_file(tmp_path):
    """The restart case: a fresh process opening the same database."""
    path = tmp_path / "nso.db"
    entry = _entry()
    nso.DesignRegistry(store=SQLiteDesignStore(path)).register("NSO-P001A", entry)

    reopened = nso.DesignRegistry(store=SQLiteDesignStore(path))
    assert reopened.known("NSO-P001A")
    assert reopened._get("NSO-P001A")["design_id"] == entry["design_id"]


def test_recipes_round_trip_as_objects_not_dicts(sqlite_registry):
    sqlite_registry.register("NSO-P002B", _entry())
    restored = sqlite_registry._get("NSO-P002B")["recipes"]
    assert all(isinstance(r, nso.DesignRecipe) for r in restored)
    assert restored[0].eye == "OD"


def test_nested_recipes_inside_candidates_round_trip(sqlite_registry):
    """Recipes are nested inside the candidate structures too."""
    sqlite_registry.register("NSO-P003C", _entry())
    candidates = sqlite_registry._get("NSO-P003C")["candidates"]
    assert isinstance(candidates["OD"]["candidates"][0]["recipe"], nso.DesignRecipe)


def test_entry_serialization_is_lossless():
    entry = _entry()
    restored = loads_entry(dumps_entry(entry))
    assert restored["recipes"] == entry["recipes"]
    assert restored["design_id"] == entry["design_id"]


def test_a_stored_design_can_still_produce_its_geometry(sqlite_registry):
    """The point of persisting: the vendor can still pull tomorrow."""
    entry = _entry()
    sqlite_registry.register(entry["design_id"], entry)
    package = sqlite_registry.manufacturing_segments(entry["design_id"], "BS")
    assert package["eyes"]["OD"]["sample_count"] > 0


def test_mutations_are_written_back_not_left_in_a_local_copy(sqlite_registry):
    """A database read returns a copy; in-place mutation would be lost."""
    entry = _entry()
    did = entry["design_id"]
    sqlite_registry.register(did, entry)

    job = sqlite_registry.submit_to_manufacturing(did)["job_id"]
    assert sqlite_registry._get(did)["jobs"] == [job]

    sqlite_registry.record_verification(did, {"verdict": "Pass"})
    assert sqlite_registry._get(did)["verifications"] == [{"verdict": "Pass"}]


def test_link_revision_persists(sqlite_registry):
    sqlite_registry.register("NSO-P010A", {"recipes": [], "revision": 1})
    sqlite_registry.register("NSO-P011B", {"recipes": [], "revision": 1})
    assert sqlite_registry.link_revision("NSO-P011B", "NSO-P010A") == 2
    stored = sqlite_registry._get("NSO-P011B")
    assert stored["revision"] == 2
    assert stored["previous_design_id"] == "NSO-P010A"


def test_unknown_design_raises_key_error(sqlite_registry):
    with pytest.raises(KeyError):
        sqlite_registry._get("NSO-P999Z")


# -- Job serials ------------------------------------------------------------ #

def test_serials_start_at_the_configured_value(tmp_path):
    store = SQLiteDesignStore(tmp_path / "a.db")
    assert store.next_job_serial() == nso.get_config().job_serial_start


def test_serials_never_repeat_within_a_store(tmp_path):
    store = SQLiteDesignStore(tmp_path / "b.db")
    serials = [store.next_job_serial() for _ in range(50)]
    assert len(set(serials)) == 50
    assert serials == sorted(serials)


def test_serials_continue_after_a_restart(tmp_path):
    """The collision case: a restart must not hand out 8721 again."""
    path = tmp_path / "c.db"
    first = [SQLiteDesignStore(path).next_job_serial() for _ in range(3)]
    after_restart = SQLiteDesignStore(path).next_job_serial()
    assert after_restart == first[-1] + 1
    assert after_restart not in first


def test_two_stores_on_one_file_do_not_collide(tmp_path):
    """Two workers of one server share the database, not a counter."""
    path = tmp_path / "d.db"
    a, b = SQLiteDesignStore(path), SQLiteDesignStore(path)
    serials = [a.next_job_serial(), b.next_job_serial(),
               a.next_job_serial(), b.next_job_serial()]
    assert len(set(serials)) == 4


def test_job_ids_are_unique_across_registry_instances(tmp_path):
    path = tmp_path / "e.db"
    entry = _entry()
    jobs = []
    for _ in range(3):
        reg = nso.DesignRegistry(store=SQLiteDesignStore(path))
        reg.register(entry["design_id"], entry)
        jobs.append(reg.submit_to_manufacturing(entry["design_id"])["job_id"])
    assert len(set(jobs)) == 3


def test_in_memory_serials_are_documented_as_process_local():
    """Kept honest: the in-memory store cannot promise cross-process serials."""
    a, b = InMemoryDesignStore(), InMemoryDesignStore()
    assert a.next_job_serial() == b.next_job_serial()
    assert "never acceptable for a real manufacturing job" in InMemoryDesignStore.__doc__


# -- Store selection -------------------------------------------------------- #

@pytest.mark.parametrize("url,expected", [
    ("", "InMemoryDesignStore"),
    ("memory://", "InMemoryDesignStore"),
    ("sqlite://:memory:", "SQLiteDesignStore"),
])
def test_store_from_url(url, expected):
    assert type(store_from_url(url)).__name__ == expected


def test_sqlite_url_resolves_to_a_real_file(tmp_path):
    path = tmp_path / "sub" / "nso.db"
    store = store_from_url(f"sqlite:///{path}")
    store.put("NSO-P020A", {"recipes": []})
    assert path.exists()


def test_unsupported_url_scheme_is_rejected():
    with pytest.raises(ValueError, match="unsupported database URL"):
        store_from_url("mysql://localhost/nso")


def test_postgres_url_selects_the_postgres_store_lazily():
    """Construction must not require a live server or a driver import."""
    from nso.manufacturing.store import PostgresDesignStore
    assert PostgresDesignStore.placeholder == "%s"
    # The adapter is unverified against a real server; say so in the docstring
    # so nobody deploys it assuming it was tested.
    assert "NOT YET EXERCISED AGAINST A REAL SERVER" in PostgresDesignStore.__doc__


def test_both_sql_backends_share_one_schema():
    """The tested SQLite behaviour is what Postgres will reproduce."""
    from nso.manufacturing.store import (
        PostgresDesignStore, SqlDesignStore, SQLiteDesignStore as S,
    )
    assert issubclass(S, SqlDesignStore)
    assert issubclass(PostgresDesignStore, SqlDesignStore)
    for method in ("put", "get", "has", "next_job_serial"):
        assert getattr(S, method) is getattr(SqlDesignStore, method)
        assert getattr(PostgresDesignStore, method) is getattr(SqlDesignStore, method)


def test_all_stores_satisfy_the_protocol(tmp_path):
    for store in (InMemoryDesignStore(), SQLiteDesignStore(tmp_path / "p.db")):
        assert isinstance(store, nso.DesignStore)


# -- The end-to-end restart scenario, in real subprocesses ------------------ #

def test_a_design_created_in_one_process_is_usable_in_another(tmp_path):
    db = tmp_path / "shared.db"
    root = str(Path(nso.__file__).parent.parent)

    create = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {root!r})
        import nso
        p = nso.PatientInput(age=11,
            od=nso.EyeInput(sphere=-3.25, axial_length=25.1),
            os=nso.EyeInput(sphere=-3.0, axial_length=24.9))
        did = nso.clinical_only(p)["design_id"]
        print(did)
        print(nso.REGISTRY.submit_to_manufacturing(did)["job_id"])
    """)
    use = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {root!r})
        import nso
        did = sys.argv[1]
        assert nso.REGISTRY.known(did), "design lost between processes"
        print(nso.REGISTRY.submit_to_manufacturing(did)["job_id"])
    """)

    env = {**os.environ, "NSO_DATABASE_URL": f"sqlite:///{db}"}
    first = subprocess.run([sys.executable, "-c", create], capture_output=True,
                           text=True, env=env, check=True).stdout.split()
    design_id, first_job = first[0], first[1]

    second_job = subprocess.run([sys.executable, "-c", use, design_id],
                                capture_output=True, text=True, env=env,
                                check=True).stdout.strip()
    assert second_job != first_job, "serial repeated across processes"


# --------------------------------------------------------------------------- #
# Higher-order roll-off (ASSUMPTIONS P0-3)
#
# Clamping the r^4 term at the reference radius kept the sag plausible but left
# a second-derivative step there — a ring of abrupt curvature change on the
# finished lens. The blend has to remove that without reintroducing the
# millimetres of sag the clamp was there to prevent.
# --------------------------------------------------------------------------- #

from nso.manufacturing.geometry import _smootherstep, _window


def _ho(r):
    """The windowed higher-order term of the BASE SURFACE.

    SA_effective(r) = C4 * r^4 * W(r), which is the form the supervisor's V2.1
    baseline asks for: the term lives inside a functional zone and tapers at
    its boundary rather than running to the edge of a 65 mm blank.
    """
    cfg = nso.get_config()
    zone = cfg.sa_reference_semi_diameter_mm
    k = 4.0 / (2 * (cfg.lens_index - 1) * 1000 * zone ** 2)
    return k * r ** 4 * _window(r, zone, cfg.sa_rolloff_blend_mm)


def _second_derivative(f, r, h=1e-3):
    return (f(r - h) - 2 * f(r) + f(r + h)) / h ** 2


@pytest.mark.parametrize("t,expected", [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)])
def test_smootherstep_endpoints(t, expected):
    assert _smootherstep(t) == pytest.approx(expected)


def test_smootherstep_is_clamped_outside_the_unit_interval():
    assert _smootherstep(-3.0) == 0.0
    assert _smootherstep(4.0) == 1.0


def test_smootherstep_is_monotonic():
    values = [_smootherstep(i / 20) for i in range(21)]
    assert values == sorted(values)


def test_no_curvature_step_at_the_zone_boundary():
    """The defect ring a hard clamp would create.

    W(r) is C2-continuous at the zone boundary, so the second derivative of
    the windowed term carries across it without a step."""
    r0 = nso.get_config().sa_reference_semi_diameter_mm
    inside = _second_derivative(_ho, r0 - 0.05)
    outside = _second_derivative(_ho, r0 + 0.05)
    assert inside == pytest.approx(outside, abs=5e-2)


def test_curvature_stays_bounded_across_the_whole_surface():
    semi = nso.get_config().optic_zone_diameter_mm / 2.0
    curvatures = [abs(_second_derivative(_ho, r))
                  for r in [0.5 + i * 0.25 for i in range(int(semi * 4) - 2)]]
    assert max(curvatures) < 1.0


def test_the_term_is_unchanged_inside_the_reference_radius():
    cfg = nso.get_config()
    k = 4.0 / (2 * (cfg.lens_index - 1) * 1000 * cfg.sa_reference_semi_diameter_mm ** 2)
    for r in (0.0, 2.0, 5.0, 9.0):
        assert _ho(r) == pytest.approx(k * r ** 4)


def test_the_term_reaches_a_plateau_and_stays_there():
    cfg = nso.get_config()
    end = cfg.sa_reference_semi_diameter_mm + cfg.sa_rolloff_blend_mm
    assert _ho(end) == pytest.approx(_ho(end + 5.0))


def test_sag_stays_within_a_plausible_spectacle_lens():
    """What the clamp was protecting against: unbounded r^4 growth."""
    zs = [pt[2] for pt in nso.surface_map(_recipe())["points"]]
    assert max(abs(z) for z in zs) < 3.0


def test_blend_width_is_configurable(restore_config):
    """The window only bites when the BACK SURFACE carries an aspheric term.
    With none specified the sag is pure prescription and the blend has nothing
    to taper — which is itself the correct behaviour."""
    from nso.config import use_config
    use_config(nso.get_config().evolve(base_surface_aspheric_sa_d=2.0))
    narrow = nso.surface_map(_recipe())["points"]
    use_config(nso.get_config().evolve(
        base_surface_aspheric_sa_d=2.0, sa_rolloff_blend_mm=12.0))
    assert nso.surface_map(_recipe())["points"] != narrow


def test_the_window_has_no_effect_without_a_surface_asphere(restore_config):
    from nso.config import use_config
    assert nso.get_config().base_surface_aspheric_sa_d == 0.0
    plain = nso.surface_map(_recipe())["points"]
    use_config(nso.get_config().evolve(sa_rolloff_blend_mm=12.0))
    assert nso.surface_map(_recipe())["points"] == plain


# --------------------------------------------------------------------------- #
# Verification names what it actually does (ASSUMPTIONS P0-5)
# --------------------------------------------------------------------------- #

def test_verification_is_named_geometric():
    assert hasattr(nso, "geometric_verification")


def test_verification_result_states_that_optics_were_not_checked():
    did = nso.clinical_only(patient())["design_id"]
    result = nso.geometric_verification(did, {"sag_error_mm": 0.001})
    assert result["verification_type"] == "geometric"
    assert result["optical_performance_verified"] is False
    assert "not measured" in result["optical_performance_note"]


def test_a_pass_verdict_does_not_claim_optical_performance():
    did = nso.clinical_only(patient())["design_id"]
    result = nso.geometric_verification(did, {"sag_error_mm": 0.001})
    assert result["verdict"] == "Pass"
    assert result["optical_performance_verified"] is False


def test_only_geometric_checks_are_accepted():
    """Nothing in the check list should imply an optical measurement."""
    did = nso.clinical_only(patient())["design_id"]
    result = nso.geometric_verification(
        did, {"sag_error_mm": 0.001, "decentration_mm": 0.1})
    names = {c["check"] for c in result["checks"]}
    assert not any(t in n for n in names for t in ("mtf", "focus", "defocus"))


def test_stored_verification_records_its_type():
    did = nso.clinical_only(patient())["design_id"]
    nso.geometric_verification(did, {"sag_error_mm": 0.001})
    assert nso.REGISTRY._get(did)["verifications"][-1]["type"] == "geometric"


def test_api_verify_response_carries_the_caveat(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", ROLE_KEYS)
    did = nso.clinical_only(patient())["design_id"]
    body = client.post("/api/manufacturing/verify",
                       json={"design_id": did, "sag_error_mm": 0.001},
                       headers={"x-api-key": "av-key"}).json()
    assert body["optical_performance_verified"] is False


def test_sqlite_three_slashes_is_relative(monkeypatch, tmp_path):
    """Three slashes is relative, four is absolute — the usual convention.
    Getting this wrong silently writes the production database somewhere else,
    which presents as data loss."""
    monkeypatch.chdir(tmp_path)
    store = store_from_url("sqlite:///relative.db")
    assert store.path == "relative.db"
    assert (tmp_path / "relative.db").exists()


def test_sqlite_four_slashes_is_absolute(monkeypatch, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.chdir(tmp_path)
    store = store_from_url(f"sqlite:///{elsewhere}/nso.db")
    assert store.path == f"{elsewhere}/nso.db"
    assert (elsewhere / "nso.db").exists()


def test_sqlite_memory_url_touches_no_disk():
    assert store_from_url("sqlite://:memory:").path == ":memory:"


# --------------------------------------------------------------------------- #
# Accommodative demand from working distance
# --------------------------------------------------------------------------- #

def test_demand_is_none_when_no_distance_was_measured():
    assert nso.accommodative_demand_d(patient()) is None


@pytest.mark.parametrize("cm,dioptres", [(25, 4.0), (40, 2.5), (50, 2.0)])
def test_demand_is_the_dioptric_definition(cm, dioptres):
    """100 / distance is a definition, not a modelling choice."""
    assert nso.accommodative_demand_d(
        patient(near_working_distance=cm)) == pytest.approx(dioptres)


def test_the_nearer_of_the_two_distances_governs():
    """The harder sustained task is the one that matters."""
    p = patient(near_working_distance=25, computer_working_distance=60)
    assert nso.accommodative_demand_d(p) == pytest.approx(4.0)


def test_closer_work_raises_accommodative_stress():
    close = nso.ai_derived_indices(patient(near_working_distance=20))
    far = nso.ai_derived_indices(patient(near_working_distance=55))
    assert close["accommodative_stress"] > far["accommodative_stress"]


def test_working_distance_reaches_the_design():
    a = nso.run_fitting(patient(near_working_distance=20))["design"]["recipes"][0]
    b = nso.run_fitting(patient(near_working_distance=55))["design"]["recipes"][0]
    assert (a.nso_modulation.zone("A").element_height_um
            != b.nso_modulation.zone("A").element_height_um)


def test_demand_is_reported_to_the_clinician():
    r = nso.clinical_only(patient(near_working_distance=25))
    assert r["accommodative_demand_d"] == pytest.approx(4.0)


# --------------------------------------------------------------------------- #
# Confidence weighs plausibility, not just coverage
# --------------------------------------------------------------------------- #

def test_an_implausible_measurement_lowers_confidence():
    """The failure this replaces: any measurement raised confidence, however
    impossible its value."""
    sane = nso.clinical_only(patient(pfv=18))["prediction_confidence"]
    absurd = nso.clinical_only(patient(pfv=95))["prediction_confidence"]
    assert absurd < sane


def test_each_further_violation_costs_more():
    one = nso.clinical_only(patient(pfv=95))["prediction_confidence"]
    two = nso.clinical_only(patient(pfv=95, hoa_rms=3.5))["prediction_confidence"]
    assert two < one


def test_out_of_range_measurements_are_named_not_just_counted():
    r = nso.clinical_only(patient(pfv=95, hoa_rms=3.5))
    named = {f["measurement"] for f in r["out_of_range_measurements"]}
    assert named == {"pfv", "hoa_rms"}
    for flagged in r["out_of_range_measurements"]:
        assert flagged["expected_low"] < flagged["expected_high"]


def test_the_explanation_says_the_prediction_is_an_extrapolation():
    r = nso.clinical_only(patient(pfv=95))
    assert any("extrapolation" in line for line in r["explainable_summary"])


def test_a_normal_patient_has_nothing_flagged():
    r = nso.clinical_only(patient())
    assert r["out_of_range_measurements"] == []
    assert r["confidence_breakdown"]["measurement_plausibility"] == 100.0


def test_confidence_breakdown_separates_the_two_halves():
    r = nso.clinical_only(patient(pfv=18, vep=1.0))
    breakdown = r["confidence_breakdown"]
    assert breakdown["measurement_coverage"] > 0
    assert breakdown["measurement_plausibility"] == 100.0


def test_out_of_range_payload_leaks_nothing():
    _assert_clean_pkg(nso.clinical_only(patient(pfv=95)))


def _assert_clean_pkg(payload):
    nso.assert_no_design_leak(payload)


def test_plausibility_ranges_are_well_formed():
    for name, (low, high) in nso.get_config().plausible_ranges.items():
        assert low < high, name


def test_both_eyes_are_checked_against_the_same_ranges():
    left_bad = nso.clinical_only(
        patient(os={"sphere": -3.0, "axial_length": 31.0}))
    assert any(f["measurement"] == "axial_length_os"
               for f in left_bad["out_of_range_measurements"])


# --------------------------------------------------------------------------- #
# The internal design console must stay out of the deploy path
# --------------------------------------------------------------------------- #

def test_no_ui_ships_inside_the_deploy_root():
    """src/backend is what deploys. The Streamlit design console that used to
    print the full recipe has been removed entirely; nothing that renders the
    Design IP should reappear here."""
    backend = Path(nso.__file__).parent.parent
    for gone in ("nso_mvp.py", "design_console.py"):
        assert not (backend / gone).exists()
    assert not (backend.parent.parent / "internal").exists()


def test_streamlit_is_not_a_deploy_dependency():
    requirements = (Path(nso.__file__).parent.parent / "requirements.txt").read_text()
    assert "streamlit" not in requirements


def test_an_implausible_reading_is_worse_than_no_reading():
    """Recording nonsense must not come out neutral. It once did: the reading
    raised coverage and lowered plausibility by exactly the same amount."""
    nothing = nso.clinical_only(patient())["prediction_confidence"]
    absurd = nso.clinical_only(patient(pfv=95))["prediction_confidence"]
    plausible = nso.clinical_only(patient(pfv=18))["prediction_confidence"]
    assert absurd < nothing < plausible


def test_an_implausible_reading_earns_no_coverage_credit():
    r = nso.clinical_only(patient(pfv=95))
    assert r["measured_domains"]["binocular_extended"] is True
    assert r["credited_domains"]["binocular_extended"] is False


def test_one_bad_reading_does_not_discredit_an_unrelated_domain():
    r = nso.clinical_only(patient(pfv=95, vep_z_score=-1.0))
    assert r["credited_domains"]["binocular_extended"] is False
    assert r["credited_domains"]["neurovisual_extended"] is True


def test_every_optional_domain_has_a_measurement_list():
    from nso.features import DOMAIN_MEASUREMENTS
    domains = set(nso.PatientInput().measured_domains())
    assert set(DOMAIN_MEASUREMENTS) == domains


# --------------------------------------------------------------------------- #
# Binocular vision: departure from norm, not from zero (ASSUMPTIONS P1-18)
# --------------------------------------------------------------------------- #

def _load(**kw):
    return nso.ai_derived_indices(patient(npc=9, **kw))["binocular_load"]


def test_esophoria_is_no_longer_invisible():
    """It read only the exo half from zero, so every esophore scored as though
    they had no binocular finding at all."""
    assert _load(near_phoria=8) > _load(near_phoria=0)
    assert _load(near_phoria=8) > _load(near_phoria=-3)


def test_strain_is_lowest_at_the_norm_not_at_zero():
    """Morgan's near norm is about 3 exo, so orthophoria is already a shift."""
    norm = nso.get_config().near_phoria_norm_d
    at_norm = _load(near_phoria=norm)
    assert at_norm < _load(near_phoria=0)
    assert at_norm < _load(near_phoria=-8)


def test_strain_rises_in_both_directions_from_the_norm():
    norm = nso.get_config().near_phoria_norm_d
    for offset in (3, 6, 9):
        assert _load(near_phoria=norm - offset) > _load(near_phoria=norm)
        assert _load(near_phoria=norm + offset) > _load(near_phoria=norm)


def test_sheard_reads_the_reserve_that_opposes_the_deviation():
    """An exophore is held by PFV, an esophore by NFV. Reading the wrong one
    would call a well-compensated patient strained."""
    exo_helped = _load(near_phoria=-12, pfv=30)
    exo_unhelped = _load(near_phoria=-12, pfv=6)
    assert exo_unhelped > exo_helped

    eso_helped = _load(near_phoria=9, nfv=25)
    eso_unhelped = _load(near_phoria=9, nfv=4)
    assert eso_unhelped > eso_helped


def test_the_wrong_reserve_is_ignored():
    """PFV must not compensate an esophoria."""
    assert _load(near_phoria=9, pfv=5) == _load(near_phoria=9, pfv=40)


@pytest.mark.parametrize("phoria,reserve,deficit", [
    (-9, 30, 0.0),      # 2 x 6 = 12, amply covered
    (-9, 6, 6.0),       # 12 - 6
    (-3, 2, 0.0),       # at norm: no demand
])
def test_sheard_deficit_formula(phoria, reserve, deficit):
    assert nso.sheard_deficit(phoria, -3.0, reserve) == pytest.approx(deficit)


def test_sheard_deficit_is_none_without_a_reserve():
    assert nso.sheard_deficit(-9, -3.0, None) is None


def test_distance_phoria_now_registers():
    assert _load(distance_phoria=-14) > _load(distance_phoria=-1)


def test_distance_phoria_weighs_less_than_near():
    """These lenses are worn for sustained near work."""
    near = _load(near_phoria=-14)
    distance = _load(distance_phoria=-14)
    assert distance < near


def test_stereoacuity_now_registers():
    assert _load(stereoacuity=400) > _load(stereoacuity=20)


def test_vergence_direction_is_signed():
    assert nso.vergence_direction(patient(near_phoria=9)) > 0     # eso
    assert nso.vergence_direction(patient(near_phoria=-12)) < 0   # exo
    assert nso.vergence_direction(
        patient(near_phoria=nso.get_config().near_phoria_norm_d)) == 0


def test_phoria_does_not_move_the_optical_target():
    """Binocular status must not reach the optical design.

    An earlier version let phoria modify SA directly. The V2.1 baseline rules
    that shape out: a binocular finding is a statement about priorities, so it
    belongs in the cost function, not baked into the lens.
    """
    eso = nso.run_fitting(patient(near_phoria=9))["design"]["recipes"][0]
    exo = nso.run_fitting(patient(near_phoria=-12))["design"]["recipes"][0]
    assert eso.nso_peak_target_d == exo.nso_peak_target_d


def test_phoria_weights_the_binocular_term_instead():
    """J = w_C*C - w_V*V - w_D*D - w_B*B, with binocular status setting w_B.

    Recalibrating a binocular finding then touches one weight, and the optical
    engine does not have to change to absorb it."""
    strained = nso.ai_derived_indices(patient(near_phoria=-14, npc=15))
    comfortable = nso.ai_derived_indices(patient(near_phoria=-3, npc=5))
    assert nso.binocular_weight(strained) > nso.binocular_weight(comfortable)


def test_the_binocular_weight_is_a_single_calibratable_number(restore_config):
    from nso.config import use_config
    indices = nso.ai_derived_indices(patient(near_phoria=-14, npc=15))
    before = nso.binocular_weight(indices)
    use_config(nso.get_config().evolve(binocular_weight_load_gain=3.0))
    assert nso.binocular_weight(indices) > before


# --------------------------------------------------------------------------- #
# Wavefront and corneal shape (ASSUMPTIONS P1-19)
# --------------------------------------------------------------------------- #

def test_residual_astigmatism_is_the_definition():
    p = patient(corneal_astigmatism=0.75,
                od={"sphere": -3.25, "cylinder": -1.5, "axial_length": 25.1})
    assert nso.residual_astigmatism(p) == pytest.approx(0.75)


def test_residual_astigmatism_is_none_without_a_corneal_reading():
    assert nso.residual_astigmatism(patient()) is None


def test_corneal_sa_departure_is_relative_to_an_average_eye():
    reference = nso.get_config().corneal_sa_reference_um
    assert nso.corneal_sa_departure(patient(corneal_sa=reference)) == pytest.approx(0)
    assert nso.corneal_sa_departure(patient(corneal_sa=reference + 0.2)) > 0


def test_an_unmeasured_eye_is_assumed_average_not_aberrated():
    assert nso.corneal_sa_departure(patient()) == 0.0
    assert nso.corneal_asphericity_departure(patient()) == 0.0
    assert nso.residual_aberration_load(patient()) == 0.0


def test_existing_aberration_lowers_adaptation():
    """An eye whose retinal image is already degraded adapts to added optical
    load less readily. This one has a non-zero coefficient because the
    direction and the rough size are both uncontroversial."""
    clean = nso.ai_derived_indices(patient(hoa_rms=0.12))["neural_adaptation"]
    aberrated = nso.ai_derived_indices(patient(hoa_rms=0.55))["neural_adaptation"]
    assert aberrated < clean


def test_coma_and_trefoil_count_toward_the_aberration_load():
    base = nso.residual_aberration_load(patient(hoa_rms=0.3))
    with_coma = nso.residual_aberration_load(patient(hoa_rms=0.3, coma=0.45))
    assert with_coma != base


def test_only_the_uncovered_modifier_still_ships_at_zero():
    """Three of these now carry values from the V2.1 baseline. Ocular
    dominance was not covered there, and inventing a rule for it would undo
    the point of asking."""
    cfg = nso.get_config()
    assert cfg.sa_corneal_compensation > 0.0
    assert cfg.sa_hoa_tolerance_gain > 0.0
    assert cfg.sa_corneal_asphericity_gain > 0.0
    assert cfg.dominance_asymmetry_gain == 0.0


def test_corneal_sa_compensation_reduces_the_added_sa(restore_config):
    from nso.config import use_config
    # Bounded modifier, not a dioptric subtraction: the supervisor's V2.1
    # guidance is 25-50% partial compensation, never 100%.
    use_config(nso.get_config().evolve(sa_corneal_compensation=0.4))
    high = nso.run_fitting(patient(corneal_sa=0.55))["design"]["recipes"][0]
    low = nso.run_fitting(patient(corneal_sa=0.05))["design"]["recipes"][0]
    assert (high.nso_modulation.zone("A").element_height_um
            < low.nso_modulation.zone("A").element_height_um)


def test_dominance_shifts_the_pair_when_enabled(restore_config):
    from nso.config import use_config
    neutral = nso.run_fitting(patient())["design"]["recipes"]
    use_config(nso.get_config().evolve(dominance_asymmetry_gain=0.3))
    biased = nso.run_fitting(patient(ocular_dominance="OD"))["design"]["recipes"]
    assert (biased[0].nso_modulation.zone("A").element_height_um
            != neutral[0].nso_modulation.zone("A").element_height_um)


def test_wavefront_quantities_are_exposed_as_features():
    values = nso.patient_features(patient(corneal_sa=0.4, hoa_rms=0.3)).values
    for name in ("corneal_sa_departure", "residual_aberration_load",
                 "corneal_asphericity_departure", "vergence_direction",
                 "dominance_od"):
        assert name in values


# --------------------------------------------------------------------------- #
# Scalarization: intermediate designs must be reachable
# --------------------------------------------------------------------------- #

def _selected_for(goal):
    p = _probe_patient()
    p.primary_goal = goal
    r = nso.run_fitting(p)
    return r["design"]["recipes"][0], r["clinical"]["design_id"]


def test_goals_produce_more_than_two_designs():
    """A weighted SUM over a discrete set always lands on an extreme point, so
    nine goals could only ever yield the gentlest design or the strongest."""
    ids = {_selected_for(goal)[1] for goal in nso.PRIMARY_GOALS}
    assert len(ids) >= 4


def test_an_intermediate_design_can_win():
    """The property a weighted sum cannot deliver."""
    cfg = nso.get_config()
    extremes = {min(cfg.candidate_offsets), max(cfg.candidate_offsets)}
    chosen_sa = {_selected_for(goal)[0].nso_peak_target_d for goal in nso.PRIMARY_GOALS}
    # At least one goal picks a design that is not at either SA extreme.
    p = _probe_patient()
    all_sa = sorted({c["recipe"].nso_peak_target_d
                     for c in nso.generate_candidates(p, "OD", nso.ai_derived_indices(p))})
    interior = set(all_sa[1:-1])
    assert chosen_sa & interior, "every goal still lands on an SA extreme"


def test_an_acuity_led_goal_takes_a_lower_optical_target():
    """A higher optical target costs acuity, so a goal that weights acuity
    should settle for less of it than a control-led goal does.

    Note it cannot make that up entirely through coverage: the coverage sweep
    is deliberately narrow, so it cannot span a whole tier step."""
    night, _ = _selected_for("Night Vision")
    myopia, _ = _selected_for("Myopia Management")
    assert night.nso_peak_target_d <= myopia.nso_peak_target_d


def test_infeasible_designs_stay_out_of_contention():
    """The penalty must exceed the whole normalized range."""
    p = _probe_patient()
    for c in nso.generate_candidates(p, "OD", nso.ai_derived_indices(p)):
        if not c["metrics"]["feasible"]:
            assert c["metrics"]["loss"] > 1.0


def test_a_term_with_no_spread_does_not_tilt_the_choice():
    from nso.design.candidates import _normalized
    assert _normalized([50.0, 50.0, 50.0]) == [0.0, 0.0, 0.0]


def test_normalization_maps_the_set_onto_zero_to_one():
    from nso.design.candidates import _normalized
    assert _normalized([10.0, 20.0, 30.0]) == [0.0, 0.5, 1.0]


# --------------------------------------------------------------------------- #
# Poisson-disk placement (ASSUMPTIONS P2-8)
# --------------------------------------------------------------------------- #

def _placement_sample(pages=2):
    r = _recipe()
    points = []
    for page in range(pages):
        points += [(e[0], e[1]) for e in nso.microstructure_map(r, page=page)["elements"]]
    return r, points


def test_no_two_elements_fall_closer_than_the_disk_radius():
    """The Poisson-disk guarantee. A jittered grid, which this used to be,
    provides no minimum spacing at all."""
    import math as _math
    from nso.manufacturing.geometry import _zone_lattice
    r, points = _placement_sample()
    _semi, radius, *_rest = _zone_lattice(r.nso_modulation.zones[0])
    assert len(points) > 500
    closest = min(
        _math.dist(a, b)
        for i, a in enumerate(points)
        for b in points[i + 1:i + 60]
    )
    assert closest >= radius * 0.999


def test_rejection_actually_happens():
    """The structural difference from a jittered grid: that emits one element
    per cell unconditionally, so nothing is ever rejected. Poisson-disk
    rejects any sample too close to a higher-priority neighbour, so a
    substantial share of cells come out empty."""
    from nso.manufacturing.geometry import (
        _resolve_row, _row_samples, _zone_lattice, _zone_seed,
    )
    r = _recipe()
    zone = r.nso_modulation.zones[0]
    semi, radius, cell, per_side, _rows = _zone_lattice(zone)
    seed = _zone_seed(r, zone)

    # A row through the middle of the zone, where cells are not clipped away.
    gi = per_side // 2
    band = {
        row: _row_samples(seed, row, cell, semi, per_side,
                          zone.inner_radius_mm, zone.outer_radius_mm)
        for row in range(gi - 2, gi + 3)
    }
    candidates = len(band[gi])
    accepted = len(_resolve_row(gi, band, radius))
    assert candidates > 100
    assert accepted < candidates * 0.8, "nothing was rejected — this is a grid"
    assert accepted > 0


def test_placement_is_reproducible_for_a_design():
    r = _recipe()
    assert nso.microstructure_map(r, page=3) == nso.microstructure_map(r, page=3)


def test_placement_differs_between_the_two_eyes():
    design = nso.run_fitting(patient(
        od={"sphere": -1.0, "axial_length": 23.4},
        os={"sphere": -7.0, "axial_length": 26.4}))["design"]
    od, os_ = design["recipes"]
    assert (nso.microstructure_map(od, page=0)["elements"]
            != nso.microstructure_map(os_, page=0)["elements"])


def test_elements_stay_inside_the_optic_zone():
    semi = nso.get_config().optic_zone_diameter_mm / 2.0
    _r, points = _placement_sample()
    for x, y in points:
        assert x * x + y * y <= semi * semi + 1e-6


def test_a_page_is_fetched_without_walking_the_ones_before_it():
    import time as _time
    r = _recipe()
    total = nso.microstructure_map(r, page=0)["total_pages"]
    start = _time.time()
    nso.microstructure_map(r, page=total - 2)
    assert _time.time() - start < 2.0


def test_the_conflict_margin_is_wide_enough_for_the_cell_size():
    """Cells are radius/sqrt(2) across, so conflicts can only reach two cells
    away. A narrower margin would silently admit violations at page seams."""
    from nso.manufacturing.geometry import _zone_lattice
    r = _recipe()
    _semi, radius, cell, *_rest = _zone_lattice(r.nso_modulation.zones[0])
    assert 2 * cell >= radius


# --------------------------------------------------------------------------- #
# Two optical channels (V2.1 baseline, section 6.2)
#
# The supervisor's answer to "where does the SA sit" was that these are two
# different optical channels and must not be merged. An earlier version took
# the profile's `3-5-4D` modulation label, treated it as surface spherical
# aberration, and folded it into the sag map.
# --------------------------------------------------------------------------- #

def test_a_recipe_carries_both_channels_separately():
    r = _recipe()
    assert r.base_surface.eye == r.nso_modulation.eye == "OD"
    assert r.base_surface.sphere == pytest.approx(-3.25)
    assert r.nso_modulation.zones


def test_the_base_surface_carries_no_nso_modulation():
    """The aspheric term is a property of the SURFACE and defaults to none."""
    r = _recipe()
    assert r.base_aspheric_sa_d == 0.0
    assert r.nso_peak_target_d > 0.0


def test_changing_the_microstructure_does_not_move_the_sag_map():
    """The test that would have caught the original conflation."""
    gentle = nso.run_fitting(patient(visual_stress_score=10))["design"]["recipes"][0]
    strong = nso.run_fitting(patient(visual_stress_score=0))["design"]["recipes"][0]
    assert (gentle.nso_modulation.zone("A").element_height_um
            != strong.nso_modulation.zone("A").element_height_um)
    assert nso.surface_map(gentle)["points"] == nso.surface_map(strong)["points"]


def test_changing_the_prescription_does_move_the_sag_map():
    a = nso.run_fitting(patient(od={"sphere": -1.0, "axial_length": 25.1}))["design"]["recipes"][0]
    b = nso.run_fitting(patient(od={"sphere": -6.0, "axial_length": 25.1}))["design"]["recipes"][0]
    assert nso.surface_map(a)["points"] != nso.surface_map(b)["points"]


def test_the_two_channels_go_to_different_vendors(client, monkeypatch):
    monkeypatch.setattr(api, "VENDOR_KEYS", ROLE_KEYS)
    did = nso.clinical_only(patient())["design_id"]

    def pull(key, segment):
        return client.post(
            "/api/manufacturing/package",
            json={"design_id": did, "segment": segment},
            headers={"x-api-key": key},
        ).json()

    front = pull("fs-key", "FS")
    back = pull("bs-key", "BS")
    assert front["package_type"] == "front_surface_geometry"
    assert back["package_type"] == "back_surface_geometry"
    assert "elements" in front["eyes"]["OD"]
    assert "points" in back["eyes"]["OD"]


# --------------------------------------------------------------------------- #
# Three-zone architecture (V2.1 baseline, sections 6.1 and 6.3)
# --------------------------------------------------------------------------- #

def test_the_reference_geometry_matches_the_supplied_baseline():
    """These are the supervisor's measured numbers, not ours."""
    ref = nso.get_config().zone_reference_geometry
    assert ref["A"] == {"diameter_um": 22.0, "length_um": 22.0,
                        "height_um": 2.2, "fill_factor_pct": 38.0}
    assert ref["B"]["diameter_um"] == 28.0
    assert ref["C"] == {"diameter_um": 32.0, "length_um": 16.0,
                        "height_um": 1.0, "fill_factor_pct": 28.0}


def test_zone_c_elements_are_elongated():
    """32 x 16 um, unlike the round elements of zones A and B."""
    r = _recipe()
    assert r.nso_modulation.zone("C").is_elongated
    assert not r.nso_modulation.zone("A").is_elongated


def test_the_profile_labels_match_the_supplied_targets():
    targets = nso.get_config().zone_optical_targets_d
    assert targets["Low"] == {"A": 1.0, "B": 3.0, "C": 2.0}
    assert targets["Medium"] == {"A": 3.0, "B": 5.0, "C": 4.0}
    assert targets["High"] == {"A": 5.0, "B": 8.0, "C": 6.0}


def test_zone_targets_are_optical_not_geometric():
    """`3-5-4D` labels an optical target. Reading it as three surface heights
    is the interpretation the V2.1 note explicitly warns against."""
    r = _recipe()
    for label, target_d in r.nso_modulation.zone_targets_d.items():
        height = r.nso_modulation.zone(label).element_height_um
        assert height != target_d, (
            f"zone {label} height equals its dioptric target — the two are "
            "being treated as the same quantity"
        )


def test_every_zone_occupies_its_own_annulus():
    zones = _recipe().nso_modulation.zones
    for inner, outer in zip(zones, zones[1:]):
        assert inner.outer_radius_mm == outer.inner_radius_mm
    assert zones[0].inner_radius_mm == 0.0


def test_a_stronger_tier_asks_for_more_from_every_zone():
    cfg = nso.get_config()
    for zone in ("A", "B", "C"):
        assert (cfg.zone_optical_targets_d["High"][zone]
                > cfg.zone_optical_targets_d["Low"][zone])


def test_the_temporal_sector_is_modulated():
    """The V2.1 baseline puts this at 25-35%."""
    modulation = _recipe().nso_modulation.temporal_modulation_pct
    assert 15.0 <= modulation <= 60.0


# --------------------------------------------------------------------------- #
# Manufacturing compensation (V2.1 baseline: HC turns 3.0 um into ~1.8 um)
# --------------------------------------------------------------------------- #

def test_the_reported_transfer_loss_is_reproduced():
    """The one datum available: hard coat leaves about 1.8 um of a 3.0 um
    design."""
    transfer = nso.manufacturing.compensation.active_transfer()
    assert transfer.as_built_height_um(3.0) == pytest.approx(1.8, abs=0.01)


def test_the_machine_is_asked_to_cut_more_than_the_design_height():
    """Pre-compensation: cutting the design value would leave the lens short."""
    report = nso.compensation_report(_recipe())
    for zone in report["zones"]:
        assert zone["cut"]["element_height_um"] > zone["design"]["element_height_um"]


def test_compensation_lands_back_on_the_design_height():
    report = nso.compensation_report(_recipe())
    for zone in report["zones"]:
        assert zone["predicted_as_built"]["element_height_um"] == pytest.approx(
            zone["design"]["element_height_um"], abs=0.01)


def test_the_transfer_model_admits_it_is_uncalibrated():
    report = nso.compensation_report(_recipe())
    assert report["transfer_model"]["calibrated"] is False


def test_the_vendor_receives_cut_heights_not_design_heights(client, monkeypatch):
    """Compensation belongs to manufacturing, so the vendor gets the number to
    cut. It is folded into the coordinates rather than shipped as a table,
    because a table would have to name zones and design heights."""
    monkeypatch.setattr(api, "VENDOR_KEYS", ROLE_KEYS)
    did = nso.clinical_only(patient())["design_id"]
    package = client.post(
        "/api/manufacturing/package",
        json={"design_id": did, "segment": "FS"},
        headers={"x-api-key": "fs-key"},
    ).json()

    page = package["eyes"]["OD"]
    assert page["geometry"] == "as_cut"
    assert "zone" not in page
    assert "cut_geometry" not in page

    recipe = nso.REGISTRY._get(did)["recipes"][0]
    design_heights = {z.element_height_um / 1000.0
                      for z in recipe.nso_modulation.zones}
    shipped = {row[4] for row in page["elements"]}
    assert shipped and not (shipped & design_heights), (
        "the vendor received design heights, not compensated ones"
    )


def test_compensation_is_not_folded_into_the_fitting_engine():
    """The 3.0 -> 1.8 um shortfall is a property of the coating line. Tuning
    the design rules to hide it would make the engine wrong everywhere else."""
    from nso.config import use_config
    original = nso.get_config()
    design_before = nso.run_fitting(patient())["design"]["recipes"][0]
    try:
        use_config(original.evolve(process_height_retention=0.4))
        design_after = nso.run_fitting(patient())["design"]["recipes"][0]
        assert (design_before.nso_modulation.zone("A").element_height_um
                == design_after.nso_modulation.zone("A").element_height_um)
    finally:
        use_config(original)


# --------------------------------------------------------------------------- #
# Bounded modifiers (V2.1 baseline, section 6.5)
# --------------------------------------------------------------------------- #

def test_corneal_sa_compensation_is_partial_never_total():
    """GIVEN: 25-50%. Corneal SA is not whole-eye SA."""
    assert 0.25 <= nso.get_config().sa_corneal_compensation <= 0.50


def test_hoa_tolerance_stays_inside_the_given_range():
    """GIVEN: reduce maximum modulation by about 10-20%."""
    assert 0.10 <= nso.get_config().sa_hoa_tolerance_gain <= 0.20


def test_corneal_asphericity_is_a_secondary_modifier():
    """GIVEN: +/-5-10%."""
    assert 0.05 <= nso.get_config().sa_corneal_asphericity_gain <= 0.10


def test_the_modifiers_move_the_design_without_dominating_it():
    """A modifier adjusts; it does not decide. Each one alone should shift the
    geometry by a few percent, not reshape it."""
    base = nso.run_fitting(patient())["design"]["recipes"][0]
    base_h = base.nso_modulation.zone("A").element_height_um

    for field, value in [("corneal_sa", 0.55), ("hoa_rms", 0.55),
                         ("corneal_eccentricity", 0.85)]:
        altered = nso.run_fitting(patient(**{field: value}))["design"]["recipes"][0]
        shift = abs(altered.nso_modulation.zone("A").element_height_um / base_h - 1)
        assert 0 < shift < 0.25, f"{field} shifted the design by {shift:.0%}"


# --------------------------------------------------------------------------- #
# The functional requirement vector (V2.1 baseline: the architecture change)
# --------------------------------------------------------------------------- #

def test_the_clinical_stage_produces_a_normalized_demand_vector():
    """Product-independent by design: the clinical layer must survive a move to
    MR-8, PC, contact lenses or a different vendor."""
    from nso.design.synthesis import modulation_target
    p = _probe_patient()
    target = modulation_target(p, "OD", nso.ai_derived_indices(p))
    for name, value in target.as_dict().items():
        assert 0.0 <= value <= 1.0, f"{name} left the unit interval"


def test_the_demand_vector_reaches_the_geometry():
    """Otherwise the whole clinical layer is decorative: the tier target is
    quantized, so without the demand vector every patient in a tier would
    receive an identical lens."""
    mild = nso.run_fitting(patient(
        od={"sphere": -1.0, "axial_length": 23.0}))["design"]["recipes"][0]
    severe = nso.run_fitting(patient(
        od={"sphere": -8.0, "axial_length": 26.5}))["design"]["recipes"][0]
    assert (severe.nso_modulation.zone("A").element_height_um
            > mild.nso_modulation.zone("A").element_height_um)


def test_the_clinical_payload_never_carries_zone_geometry():
    payload = nso.clinical_only(patient())
    nso.assert_no_design_leak(payload)
    text = json.dumps(payload).lower()
    for term in ("element_height", "element_diameter", "zone_a", "fill_factor",
                 "aspheric", "3-5-4"):
        assert term not in text


# --------------------------------------------------------------------------- #
# Contrast sensitivity schema (V2.1 baseline, section 6.6)
# --------------------------------------------------------------------------- #

def test_a_measurement_carries_its_own_protocol():
    m = nso.CsfMeasurement(
        spatial_frequency_cpd=[1.5, 3.0, 6.0, 12.0, 18.0],
        log_cs=[1.75, 1.90, 1.80, 1.40, 0.90],
        device="CSV-1000", test_protocol="photopic",
    )
    record = m.as_record()
    for key in ("device", "test_protocol", "spatial_frequency_cpd",
                "raw_sensitivity", "log_cs", "normalization_reference",
                "csf_auc", "csf_slope", "csf_centroid_cpd"):
        assert key in record


def test_the_same_curve_at_different_frequencies_reads_differently():
    """The failure the fixed 1.5/6/18 assumption caused: two clinics with
    different protocols got the same AUC for different measurements."""
    curve = [1.75, 1.90, 1.80, 1.40, 0.90]
    narrow = nso.CsfMeasurement(spatial_frequency_cpd=[3, 4, 6, 9, 12], log_cs=curve)
    wide = nso.CsfMeasurement(spatial_frequency_cpd=[1, 2, 4, 8, 16], log_cs=curve)
    assert narrow.slope() != wide.slope()
    assert narrow.centroid_cpd() != wide.centroid_cpd()


def test_raw_sensitivity_and_log_cs_convert_to_each_other():
    from_raw = nso.CsfMeasurement(
        spatial_frequency_cpd=[1.5, 6.0], raw_sensitivity=[100.0, 10.0])
    assert from_raw.log_cs == pytest.approx([2.0, 1.0])
    from_log = nso.CsfMeasurement(
        spatial_frequency_cpd=[1.5, 6.0], log_cs=[2.0, 1.0])
    assert from_log.raw_sensitivity == pytest.approx([100.0, 10.0])


def test_an_assumed_protocol_is_flagged_as_assumed():
    """The assumption travels with the data instead of hiding in the engine."""
    assumed = nso.csf_from_triplet(90, 70, 40)
    stated = nso.csf_from_triplet(90, 70, 40, frequencies=[1.0, 4.0, 16.0])
    assert assumed.frequencies_assumed is True
    assert stated.frequencies_assumed is False


def test_the_band_is_a_fallback_not_a_measurement():
    """Low/Mid/High is a clinician's impression. It must not be recorded as
    though an instrument produced it."""
    banded = patient(csf_band="Low")
    measured = patient(csf_low=90.0, csf_mid=70.0, csf_high=40.0)
    assert banded.csf_is_measured is False
    assert measured.csf_is_measured is True


def test_the_payload_says_whether_csf_was_measured():
    banded = nso.clinical_only(patient(csf_band="Low"))["csf_protocol"]
    measured = nso.clinical_only(
        patient(csf_low=90.0, csf_mid=70.0, csf_high=40.0,
                csf_device="CSV-1000"))["csf_protocol"]
    assert banded["measured"] is False
    assert "band" in banded["source"]
    assert measured["measured"] is True
    assert measured["device"] == "CSV-1000"


def test_a_measured_curve_beats_the_band():
    with_band = patient(csf_band="Low").csf_value()
    with_curve = patient(csf_band="Low", csf_low=95.0, csf_mid=95.0,
                         csf_high=95.0).csf_value()
    assert with_curve > with_band


def test_the_band_mapping_is_configurable(restore_config):
    """It must not harden into a device-specific constant."""
    from nso.config import use_config
    before = patient(csf_band="Low").csf_value()
    use_config(nso.get_config().evolve(
        csf_bands={"Low": 20.0, "Mid": 70.0, "High": 90.0}))
    assert patient(csf_band="Low").csf_value() != before


def test_the_measured_frequencies_reach_the_design():
    """Slope is per decade of spatial frequency, so the protocol changes what
    a roll-off costs — a difference of two raw values cannot see that."""
    steep = nso.ai_derived_indices(patient(
        csf_low=90.0, csf_mid=70.0, csf_high=40.0,
        csf_frequencies_cpd=[3.0, 6.0, 12.0]))
    gentle = nso.ai_derived_indices(patient(
        csf_low=90.0, csf_mid=70.0, csf_high=40.0,
        csf_frequencies_cpd=[1.0, 4.0, 16.0]))
    assert (steep["spatial_frequency_sensitivity"]
            < gentle["spatial_frequency_sensitivity"])


def test_a_short_or_ragged_curve_is_rejected_rather_than_guessed():
    assert nso.CsfMeasurement(spatial_frequency_cpd=[6.0], log_cs=[1.5]).auc() is None
    mismatched = nso.CsfMeasurement(
        spatial_frequency_cpd=[1.5, 6.0, 18.0], log_cs=[1.5, 1.2])
    assert mismatched.is_usable is False


# --------------------------------------------------------------------------- #
# The pipeline (V2.1 baseline: the architecture change)
# --------------------------------------------------------------------------- #

def test_the_pipeline_runs_every_stage():
    result = nso.run_pipeline(_probe_patient())
    for stage in nso.STAGES:
        assert result.stage(stage), f"stage {stage} produced nothing"


def test_the_pipeline_can_stop_early():
    """A caller that only wants the demand vector should not pay for geometry."""
    early = nso.run_pipeline(_probe_patient(), through="functional_requirement")
    assert early.functional_requirement
    assert not early.geometry_projection
    assert not early.manufacturing_compensation


def test_an_unknown_stage_is_rejected():
    with pytest.raises(KeyError):
        nso.run_pipeline(_probe_patient(), through="not_a_stage")


def test_the_first_two_stages_are_product_independent():
    """The boundary that lets the clinical layer survive a move to MR-8, PC or
    contact lenses: nothing above it may mention lens geometry."""
    result = nso.run_pipeline(_probe_patient(), through="functional_requirement")
    text = json.dumps({
        "phenotype": result.clinical_phenotype["phenotype"],
        "indices": result.clinical_phenotype["indices"],
        "requirement": {k: v.as_dict()
                        for k, v in result.functional_requirement.items()},
    }).lower()
    for term in ("zone", "element", "fill_factor", "micro", "sag", "height_um"):
        assert term not in text


def test_the_demand_vector_stays_in_the_unit_interval():
    result = nso.run_pipeline(_probe_patient(), through="functional_requirement")
    for eye, target in result.functional_requirement.items():
        for name, value in target.as_dict().items():
            assert 0.0 <= value <= 1.0, f"{eye}.{name} left the unit interval"


def test_a_process_change_moves_only_the_last_stages():
    """The 3.0 -> 1.8 um coating loss must not reach back into the design."""
    from nso.config import use_config
    original = nso.get_config()
    p = _probe_patient()
    before = nso.run_pipeline(p)
    try:
        use_config(original.evolve(process_height_retention=0.4))
        after = nso.run_pipeline(p)
        assert (before.optical_target == after.optical_target)
        assert (before.design_profile["recipes"]["OD"].nso_modulation.zones
                == after.design_profile["recipes"]["OD"].nso_modulation.zones)
        assert (before.manufacturing_compensation["OD"]["zones"][0]["cut"]
                != after.manufacturing_compensation["OD"]["zones"][0]["cut"])
    finally:
        use_config(original)


def test_a_clinical_change_moves_the_whole_chain():
    mild = nso.run_pipeline(patient(od={"sphere": -1.0, "axial_length": 23.0}))
    severe = nso.run_pipeline(patient(od={"sphere": -8.0, "axial_length": 26.5}))
    assert (mild.functional_requirement["OD"].control_demand
            < severe.functional_requirement["OD"].control_demand)
    assert (mild.design_profile["recipes"]["OD"].nso_modulation.zone("A").element_height_um
            < severe.design_profile["recipes"]["OD"].nso_modulation.zone("A").element_height_um)


def test_geometry_projection_keeps_the_two_channels_apart():
    projection = nso.run_pipeline(_probe_patient()).geometry_projection["OD"]
    assert "points" in projection["base_surface"]
    assert "elements" in projection["nso_modulation"]
    assert "elements" not in projection["base_surface"]
    assert "points" not in projection["nso_modulation"]


def test_the_pipeline_description_matches_what_runs():
    described = [stage["stage"] for stage in nso.describe_pipeline()]
    assert described == list(nso.STAGES)


def test_the_pipeline_is_reported_by_the_health_endpoint(client):
    assert client.get("/api/health").json()["pipeline"] == list(nso.STAGES)


# --------------------------------------------------------------------------- #
# Neurovisual: record, normalize, then credit — in that order (V2.1 § 6.7)
# --------------------------------------------------------------------------- #

def test_confidence_does_not_rise_for_having_run_a_test():
    """The V2.1 baseline is explicit: confidence must not increase mechanically
    just because a VEP was performed. A bare number carries no stimulus, unit
    or normative reference, so nothing was learned from it."""
    base = nso.clinical_only(patient())["prediction_confidence"]
    bare = nso.clinical_only(patient(vep=1.5))["prediction_confidence"]
    assert bare == base


def test_an_interpretable_result_does_raise_confidence():
    base = nso.clinical_only(patient())["prediction_confidence"]
    scored = nso.clinical_only(
        patient(vep_z_score=-1.2))["prediction_confidence"]
    assert scored > base


def test_eye_tracking_sub_metrics_are_interpretable_on_their_own():
    """Each has a defined scale, unlike a single opaque 'eye tracking' number."""
    base = nso.clinical_only(patient())["prediction_confidence"]
    tracked = nso.clinical_only(
        patient(fixation_stability=12.0, blink_rate=14.0))["prediction_confidence"]
    assert tracked > base


def test_a_bare_value_is_still_recorded():
    """record -> normalize -> confidence, in that order. An uninterpretable
    value is kept for the future dataset even though it cannot be used now."""
    p = patient(vep=1.5)
    assert p.neurovisual_recorded is True
    assert p.has_interpretable_neurovisual is False


def test_the_payload_distinguishes_recorded_from_used():
    """A clinician who ran a VEP deserves to see which of the two happened."""
    assert nso.clinical_only(patient())["neurovisual_status"] == "not_recorded"
    assert nso.clinical_only(
        patient(vep=1.5))["neurovisual_status"] == "recorded_not_interpretable"
    assert nso.clinical_only(
        patient(vep_z_score=-1.2))["neurovisual_status"] == "interpretable"


def test_the_structured_vep_schema_is_recorded():
    """stimulus -> amplitude -> latency -> interocular difference -> z-score."""
    fields = set(nso.PatientInput.__dataclass_fields__)
    assert {"vep_stimulus", "vep_amplitude_uv", "vep_latency_ms",
            "vep_interocular_difference_ms", "vep_z_score"} <= fields


def test_eye_tracking_sub_metrics_are_recorded_individually():
    fields = set(nso.PatientInput.__dataclass_fields__)
    assert {"fixation_stability", "blink_rate", "vergence_stability",
            "pupil_dynamics", "gaze_distribution"} <= fields


def test_neurovisual_features_exist_for_the_future_optimizer():
    """Logged now so the dataset is there when eye tracking is ready to enter
    the optimizer, which the baseline expects it to do first."""
    values = nso.patient_features(
        patient(fixation_stability=12.0, vep_z_score=-1.2)).values
    for name in ("fixation_stability", "blink_rate", "vergence_stability",
                 "pupil_dynamics", "gaze_distribution", "vep_z_score",
                 "vep_latency_ms", "erg_z_score"):
        assert name in values


# --------------------------------------------------------------------------- #
# The two optical channels are separate objects in the database (V2.1 § 6.2)
# --------------------------------------------------------------------------- #

def test_the_database_stores_two_independent_optical_objects():
    """Keeping them apart in storage is what stops a later reader treating
    microstructure modulation as base-surface sag."""
    design_id = nso.clinical_only(patient())["design_id"]
    entry = nso.REGISTRY._get(design_id)
    assert "base_surface_optical_profile" in entry
    assert "nso_spatial_modulation_profile" in entry


def test_the_stored_channels_hold_different_quantities():
    design_id = nso.clinical_only(patient())["design_id"]
    entry = nso.REGISTRY._get(design_id)
    base = entry["base_surface_optical_profile"]["OD"]
    nso_profile = entry["nso_spatial_modulation_profile"]["OD"]
    assert "sphere" in base and "zones" not in base
    assert "zones" in nso_profile and "sphere" not in nso_profile


def test_the_stored_channels_survive_a_round_trip(tmp_path):
    from nso.manufacturing.store import SQLiteDesignStore
    registry = nso.DesignRegistry(store=SQLiteDesignStore(tmp_path / "ch.db"))
    entry = nso.run_fitting(patient())["design"]
    registry.register(entry["design_id"], entry)
    restored = registry._get(entry["design_id"])
    assert restored["base_surface_optical_profile"]["OD"]["sphere"] == \
        entry["base_surface_optical_profile"]["OD"]["sphere"]
