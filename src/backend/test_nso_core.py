"""
Tests for the NSO MVP computation logic.

These cover the pure computation kernel in ``nso_core`` (no UI is
executed on import, since the UI lives inside ``main()``). Run with:

    cd nso_software
    .venv/bin/python -m pytest -q
"""

import pytest

import nso_core as m


# --------------------------------------------------------------------------- #
# norm
# --------------------------------------------------------------------------- #

def test_norm_basic_and_clipping():
    assert m.norm(5, 0, 10) == pytest.approx(0.5)
    assert m.norm(0, 0, 10) == 0.0
    assert m.norm(10, 0, 10) == 1.0
    # values outside the range are clipped to [0, 1]
    assert m.norm(-5, 0, 10) == 0.0
    assert m.norm(50, 0, 10) == 1.0


# --------------------------------------------------------------------------- #
# compute_baseline_risk
# --------------------------------------------------------------------------- #

def test_baseline_risk_stays_in_unit_interval():
    # Extreme "high risk" patient and "low risk" patient must both stay in [0, 1].
    high = m.compute_baseline_risk(
        age=6, al=27.0, myopia=-8, near_hours=10, pupil=6.5, outdoor_hours=0
    )
    low = m.compute_baseline_risk(
        age=18, al=22.0, myopia=-0.5, near_hours=0, pupil=3.0, outdoor_hours=4
    )
    assert 0.0 <= low <= high <= 1.0


def test_baseline_risk_monotonic_in_axial_length():
    base = dict(age=10, myopia=-3, near_hours=5, pupil=4.5, outdoor_hours=1)
    low_al = m.compute_baseline_risk(al=23.0, **base)
    high_al = m.compute_baseline_risk(al=26.0, **base)
    assert high_al > low_al


# --------------------------------------------------------------------------- #
# evaluate_profiles
# --------------------------------------------------------------------------- #

def test_evaluate_profiles_sorted_and_complete():
    df = m.evaluate_profiles(
        baseline_risk=0.6, risk_csf=0.3, comfort_risk=0.2, risk_pupil=0.4
    )
    # One row per configured profile.
    assert set(df["Profile"]) == set(m.NSO_PROFILES)
    # Sorted ascending by Loss (best first).
    assert list(df["Loss"]) == sorted(df["Loss"])
    # Helper columns the indices depend on must be present.
    for col in ("DensityNumeric", "SAStrength", "Predicted Control Index"):
        assert col in df.columns


# --------------------------------------------------------------------------- #
# next_profile_from_al
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "delta, current, expected_next",
    [
        (0.05, "Low", "Low"),        # stable -> maintain
        (0.10, "Medium", "Medium"),  # boundary still "maintain"
        (0.15, "Low", "Medium"),     # mild progression -> +1 level from Low
        (0.15, "Medium", "High"),    # mild progression -> +1 level from Medium
        (0.30, "Low", "High"),       # fast progression -> escalate
    ],
)
def test_next_profile_from_al(delta, current, expected_next):
    _, next_profile = m.next_profile_from_al(delta, current)
    assert next_profile == expected_next


def test_next_profile_from_al_floating_point_boundary():
    # 24.55 - 24.50 over 6 months annualizes to 0.10000000000000142 in float,
    # which must still count as the "maintain" boundary, not tip into "increase".
    annualized = (24.55 - 24.50) * (12 / 6)
    assert annualized > 0.10  # confirms the raw float noise exists
    advice, next_profile = m.next_profile_from_al(annualized, "Medium")
    assert advice == "Maintain current NSO profile"
    assert next_profile == "Medium"


# --------------------------------------------------------------------------- #
# Neural optical indices
# --------------------------------------------------------------------------- #

def test_entropy_score_exact():
    # 0.35*33 + 0.25*(0.10)*100 + 0.30*3*10 + 0.10*3*10
    # = 11.55 + 2.5 + 9.0 + 3.0 = 26.05
    assert m.entropy_score(33, 1.10, 3.0) == pytest.approx(26.05)


def test_entropy_score_clipped_to_100():
    assert m.entropy_score(density=100, temporal_multiplier=2.0, sa_strength=20) == 100.0


def test_neural_adaptation_exact_and_bounds():
    # 0.35*80 + 0.30*75 + 0.20*(100-26.05) + 0.15*(100-18)
    # = 28 + 22.5 + 14.79 + 12.3 = 77.59
    val = m.neural_adaptation(age=9, comfort_tolerance=80, csf_quality=75, entropy=26.05)
    assert val == pytest.approx(77.59)
    assert 0.0 <= val <= 100.0


def test_visual_stress_exact():
    # 0.35*26.05 + 0.25*3*10 + 0.25*(4.5-4)*15 + 0.15*(100-80)
    # = 9.1175 + 7.5 + 1.875 + 3 = 21.4925
    val = m.visual_stress(
        entropy=26.05, sa_strength=3.0, pupil_mm=4.5, comfort_tolerance=80
    )
    assert val == pytest.approx(21.4925)


def test_visual_stress_ignores_small_pupil():
    # pupil <= 4.0 mm contributes nothing from the pupil term.
    a = m.visual_stress(entropy=50, sa_strength=5, pupil_mm=3.0, comfort_tolerance=70)
    b = m.visual_stress(entropy=50, sa_strength=5, pupil_mm=4.0, comfort_tolerance=70)
    assert a == pytest.approx(b)


def test_dynamic_robustness_exact():
    # 0.30*85 + 0.25*80 + 0.25*75 + 0.20*90 = 25.5 + 20 + 18.75 + 18 = 82.25
    assert m.dynamic_robustness(85, 80, 75, 90) == pytest.approx(82.25)


# --------------------------------------------------------------------------- #
# responder_prediction
# --------------------------------------------------------------------------- #

def test_responder_score_formula():
    # 0.5*80 + 0.25*68 + 0.25*72 = 40 + 17 + 18 = 75
    score, tier, _ = m.responder_prediction(
        control_index=80, adaptation_score=68, entropy=72
    )
    assert score == pytest.approx(75.0)
    assert tier == "Good"


@pytest.mark.parametrize(
    "ci, ad, en, tier",
    [
        (100, 100, 100, "Good"),      # max
        (100, 50, 50, "Good"),        # exactly 75 -> Good (>= boundary)
        (60, 60, 60, "Moderate"),     # 60
        (70, 40, 40, "Moderate"),     # exactly 55 -> Moderate (>= boundary)
        (40, 40, 40, "Poor"),         # 40
        (0, 0, 0, "Poor"),            # min
    ],
)
def test_responder_classification_boundaries(ci, ad, en, tier):
    _, got_tier, _ = m.responder_prediction(ci, ad, en)
    assert got_tier == tier


def test_responder_detail_fields_per_tier():
    _, _, good = m.responder_prediction(100, 100, 100)
    assert good["al_reduction"] == "55~75%"
    assert good["follow_up"] == "6 months"
    assert good["note"] == ""

    _, _, poor = m.responder_prediction(0, 0, 0)
    assert poor["al_reduction"] == "<30%"
    assert "SA" in poor["note"]  # recommends increasing SA


def test_responder_score_clipped():
    score, _, _ = m.responder_prediction(
        control_index=200, adaptation_score=200, entropy=200
    )
    assert score == 100.0


def test_higher_control_gives_higher_responder_score():
    low, _, _ = m.responder_prediction(40, 70, 30)
    high, _, _ = m.responder_prediction(90, 70, 30)
    assert high > low


# --------------------------------------------------------------------------- #
# V2 dual-path: control_score / adaptation_score
# --------------------------------------------------------------------------- #

def test_control_score_exact():
    # age_factor=0.75, al_factor=0.5, near_factor=4/9, outdoor_factor=0.25
    # 0.50*55 + 0.20*50 + 0.15*75 + 0.10*(400/9) - 0.10*25
    val = m.control_score(
        age=9, al=24.5, near_hours=5, outdoor_hours=1, profile_strength=55
    )
    assert val == pytest.approx(50.69444, abs=1e-4)
    assert 0.0 <= val <= 100.0


def test_control_score_increases_with_profile_strength():
    base = dict(age=9, al=24.5, near_hours=5, outdoor_hours=1)
    assert m.control_score(profile_strength=75, **base) > m.control_score(
        profile_strength=35, **base
    )


def test_control_score_outdoor_is_protective():
    base = dict(age=9, al=24.5, near_hours=5, profile_strength=55)
    # More outdoor time lowers the (need-driven) control score.
    assert m.control_score(outdoor_hours=4, **base) < m.control_score(
        outdoor_hours=0, **base
    )


def test_adaptation_score_exact():
    # pupil_factor=1.5/3.5; 0.35*70 + 0.25*80 + 0.20*70 + 0.10*60 + 0.10*(100-pf*100)
    val = m.adaptation_score(
        pupil_mm=4.5,
        entropy=40,
        visual_stress_index=30,
        neural_adaptation_score=70,
        comfort=80,
    )
    assert val == pytest.approx(70.21429, abs=1e-4)
    assert 0.0 <= val <= 100.0


def test_adaptation_score_drops_with_visual_stress():
    base = dict(pupil_mm=4.5, entropy=40, neural_adaptation_score=70, comfort=80)
    assert m.adaptation_score(visual_stress_index=80, **base) < m.adaptation_score(
        visual_stress_index=10, **base
    )


# --------------------------------------------------------------------------- #
# V2 dual-path: classification helpers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "score, expected",
    [(60, "High"), (60.0, "High"), (59.99, "Low"), (100, "High"), (0, "Low")],
)
def test_classify_high_low(score, expected):
    assert m.classify_high_low(score) == expected


@pytest.mark.parametrize(
    "score, band",
    [(80, "55~75%"), (75, "55~75%"), (60, "30~55%"), (50, "30~55%"), (49, "<30%")],
)
def test_al_reduction_estimate(score, band):
    assert m.al_reduction_estimate(score) == band


@pytest.mark.parametrize(
    "score, risk",
    [(70, "Low"), (60, "Low"), (50, "Moderate"), (40, "Moderate"), (30, "High")],
)
def test_adaptation_risk(score, risk):
    assert m.adaptation_risk(score) == risk


# --------------------------------------------------------------------------- #
# V2 dual-path: 2x2 decision matrix
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "ctrl, adapt, case",
    [
        ("High", "High", "A"),
        ("High", "Low", "B"),
        ("Low", "High", "C"),
        ("Low", "Low", "D"),
    ],
)
def test_dual_path_decision_cases(ctrl, adapt, case):
    got_case, title, recs = m.dual_path_decision(ctrl, adapt)
    assert got_case == case
    assert isinstance(title, str) and title
    assert len(recs) >= 1


def test_dual_path_case_a_is_immediate_prescription():
    _, _, recs = m.dual_path_decision("High", "High")
    assert any("Immediate prescription" in r for r in recs)


def test_dual_path_case_d_offers_alternatives():
    _, _, recs = m.dual_path_decision("Low", "Low")
    joined = " ".join(recs)
    assert "Orthokeratology" in joined and "Atropine therapy" in joined


# --------------------------------------------------------------------------- #
# V2 dual-path: 2x2 grid layout
# --------------------------------------------------------------------------- #

def test_grid_shape_is_2x2():
    grid = m.dual_path_grid("High", "High")
    assert len(grid) == 2
    assert all(len(row) == 2 for row in grid)


def test_grid_cell_positions_map_to_correct_cases():
    # Control on x-axis (Low left, High right); Adaptation on y-axis (High top).
    grid = m.dual_path_grid("High", "High")
    top_left, top_right = grid[0]
    bottom_left, bottom_right = grid[1]
    assert top_left["case"] == "C"      # Low control / High adaptation
    assert top_right["case"] == "A"     # High control / High adaptation
    assert bottom_left["case"] == "D"   # Low control / Low adaptation
    assert bottom_right["case"] == "B"  # High control / Low adaptation


@pytest.mark.parametrize(
    "ctrl, adapt, expected_case",
    [
        ("High", "High", "A"),
        ("High", "Low", "B"),
        ("Low", "High", "C"),
        ("Low", "Low", "D"),
    ],
)
def test_grid_highlights_exactly_one_active_cell(ctrl, adapt, expected_case):
    grid = m.dual_path_grid(ctrl, adapt)
    active = [cell for row in grid for cell in row if cell["active"]]
    assert len(active) == 1
    assert active[0]["case"] == expected_case
    assert active[0]["control_class"] == ctrl
    assert active[0]["adaptation_class"] == adapt


def test_grid_labels_match_case_labels():
    grid = m.dual_path_grid("Low", "Low")
    for row in grid:
        for cell in row:
            assert cell["label"] == m.DUAL_PATH_SHORT_LABELS[cell["case"]]


# --------------------------------------------------------------------------- #
# v0.3: probability-driven 2x2 matrix
# --------------------------------------------------------------------------- #

def test_quadrant_probabilities_sum_to_one():
    for pc in (0.1, 0.5, 0.9):
        for pa in (0.2, 0.5, 0.8):
            probs = m.matrix_quadrant_probabilities(pc, pa)
            assert sum(probs.values()) == pytest.approx(1.0)
            assert all(0.0 <= v <= 1.0 for v in probs.values())


def test_quadrant_probabilities_are_the_joint():
    probs = m.matrix_quadrant_probabilities(0.7, 0.6)
    assert probs["A"] == pytest.approx(0.7 * 0.6)
    assert probs["B"] == pytest.approx(0.7 * 0.4)
    assert probs["C"] == pytest.approx(0.3 * 0.6)
    assert probs["D"] == pytest.approx(0.3 * 0.4)


@pytest.mark.parametrize(
    "pc, pa, expected",
    [
        (0.9, 0.9, "A"),  # high control, high adaptation
        (0.9, 0.2, "B"),  # high control, low adaptation
        (0.2, 0.9, "C"),  # low control, high adaptation
        (0.2, 0.2, "D"),  # low control, low adaptation
    ],
)
def test_recommended_case_is_argmax(pc, pa, expected):
    probs = m.matrix_quadrant_probabilities(pc, pa)
    assert m.recommended_case(probs) == expected


def test_case_classes_consistent_with_decision():
    # CASE_CLASSES must invert dual_path_decision.
    for case, (cc, ac) in m.CASE_CLASSES.items():
        assert m.dual_path_decision(cc, ac)[0] == case


# --------------------------------------------------------------------------- #
# V3: calibrated probability (the fixed sigmoid)
# --------------------------------------------------------------------------- #

def test_probability_is_half_at_center():
    assert m.score_to_probability(50) == pytest.approx(0.5)


def test_probability_in_open_unit_interval():
    for s in (0, 25, 50, 75, 100):
        p = m.score_to_probability(s)
        assert 0.0 < p < 1.0


def test_probability_is_monotonic():
    assert m.score_to_probability(40) < m.score_to_probability(60)


def test_probability_does_not_saturate():
    # The whole point of the fix: a high score must NOT collapse to ~100%.
    # A plain sigmoid(87) would be ~1.0; the calibrated one stays well below.
    p = m.score_to_probability(87)
    assert 0.85 < p < 0.97


# --------------------------------------------------------------------------- #
# V3: 3-component responder score
# --------------------------------------------------------------------------- #

def test_responder_score_v3_exact():
    # 0.45*60 + 0.35*70 + 0.20*50 = 27 + 24.5 + 10 = 61.5
    assert m.responder_score_v3(60, 70, 50) == pytest.approx(61.5)


def test_responder_score_v3_uses_all_three_components():
    base = m.responder_score_v3(60, 60, 60)
    assert m.responder_score_v3(80, 60, 60) > base  # control matters
    assert m.responder_score_v3(60, 80, 60) > base  # adaptation matters
    assert m.responder_score_v3(60, 60, 80) > base  # entropy matters


# --------------------------------------------------------------------------- #
# V3: Good/Moderate/Poor probabilities
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("score", [0, 40, 55, 65, 75, 90, 100])
def test_responder_probabilities_sum_to_one(score):
    probs = m.responder_probabilities(score)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert all(0.0 <= p <= 1.0 for p in probs.values())


def test_high_score_peaks_good_low_score_peaks_poor():
    high = m.responder_probabilities(90)
    low = m.responder_probabilities(30)
    assert max(high, key=high.get) == "Good"
    assert max(low, key=low.get) == "Poor"


def test_prob_good_increases_with_score():
    assert (
        m.responder_probabilities(85)["Good"]
        > m.responder_probabilities(60)["Good"]
    )


# --------------------------------------------------------------------------- #
# V3: prediction confidence
# --------------------------------------------------------------------------- #

def test_confidence_in_range_and_flags_out_of_range():
    probs = {"Good": 0.8, "Moderate": 0.15, "Poor": 0.05}
    # Typical patient, good design -> all inputs in range, confident.
    conf, need = m.prediction_confidence(10, 24.5, 4.5, 5, 80, 75, probs, 0.9)
    assert 0.0 <= conf <= 100.0
    assert need is False
    # Out-of-range AL -> lower confidence, flag raised.
    conf2, need2 = m.prediction_confidence(10, 30.0, 4.5, 5, 80, 75, probs, 0.9)
    assert conf2 < conf
    assert need2 is True


def test_low_design_quality_cuts_confidence_and_flags():
    probs = {"Good": 0.8, "Moderate": 0.15, "Poor": 0.05}
    high = m.prediction_confidence(10, 24.5, 4.5, 5, 80, 75, probs, 0.9)[0]
    low_conf, low_need = m.prediction_confidence(10, 24.5, 4.5, 5, 80, 75, probs, 0.2)
    assert low_conf < high        # weak design lowers confidence
    assert low_need is True        # and flags need-more / adjustment


# --------------------------------------------------------------------------- #
# v0.3: entropy/robustness probability + recommended action
# --------------------------------------------------------------------------- #

def test_entropy_robustness_probability_monotonic_and_bounded():
    low = m.entropy_robustness_probability(20, 20)
    high = m.entropy_robustness_probability(90, 90)
    assert 0.0 < low < high < 1.0
    # Mid design quality (50/50) sits at 0.5.
    assert m.entropy_robustness_probability(50, 50) == pytest.approx(0.5)


def test_recommended_action_prioritises_low_design():
    # Low ER probability -> design-adjustment message regardless of case.
    action = m.recommended_action("A", er_probability=0.2, need_more=True)
    assert "design" in action.lower()


def test_recommended_action_falls_back_to_case_when_confident():
    action = m.recommended_action("D", er_probability=0.9, need_more=False)
    assert action == m.CASE_ACTIONS["D"]


def test_recommended_action_flags_low_confidence():
    action = m.recommended_action("A", er_probability=0.9, need_more=True)
    assert "follow-up" in action.lower()


# --------------------------------------------------------------------------- #
# V3: explainable-AI contributors
# --------------------------------------------------------------------------- #

def test_top_contributors_returns_sorted_top_n():
    contribs = m.top_contributors(
        age=8, al=26, near_hours=9, outdoor_hours=0,
        profile_strength=70, entropy=60, adaptation=70, top_n=3,
    )
    assert len(contribs) == 3
    mags = [abs(c["percent"]) for c in contribs]
    assert mags == sorted(mags, reverse=True)


def test_contributor_signs_make_sense():
    contribs = m.top_contributors(
        age=7, al=26, near_hours=9, outdoor_hours=4,
        profile_strength=75, entropy=60, adaptation=70, top_n=7,
    )
    by_factor = {c["factor"]: c["percent"] for c in contribs}
    assert by_factor["Age"] > 0       # young -> positive
    assert by_factor["Outdoor"] < 0   # lots of outdoor -> protective, lowers score


def test_contributor_percents_are_shares_summing_to_100():
    # Over the full factor set, absolute shares should sum to ~100%.
    contribs = m.top_contributors(
        age=7, al=26, near_hours=9, outdoor_hours=4,
        profile_strength=75, entropy=60, adaptation=70, top_n=7,
    )
    total_abs = sum(abs(c["percent"]) for c in contribs)
    assert total_abs == pytest.approx(100, abs=2)  # rounding tolerance


def test_expected_al_reduction_mm():
    assert m.expected_al_reduction_mm(0) == 0.0
    # 0.30 * (100/100 * 0.75) = 0.225 -> ~0.22 mm/year
    assert m.expected_al_reduction_mm(100) == pytest.approx(0.225, abs=0.01)
    assert m.expected_al_reduction_mm(80) > m.expected_al_reduction_mm(40)


# --------------------------------------------------------------------------- #
# Orchestration (run_prediction / run_followup) — used by the FastAPI backend
# --------------------------------------------------------------------------- #

def _default_prediction():
    return m.run_prediction(
        age=9, al=24.5, myopia=-3.0, pupil=4.5,
        near_hours=5, outdoor_hours=1, csf_score=75, comfort_score=80,
    )


def test_run_prediction_shape_and_ranges():
    r = _default_prediction()
    # All the keys the frontend/API consumes are present.
    for key in [
        "profile", "expected_al_reduction_mm_per_year", "recommended_follow_up",
        "control_probability", "adaptation_probability", "quadrant_probabilities",
        "recommended_case", "entropy_robustness_probability", "prediction_confidence",
        "recommended_action", "responder_probabilities", "top_contributors",
    ]:
        assert key in r
    # Quadrant probabilities sum to 1 and the recommended case is their argmax.
    assert sum(r["quadrant_probabilities"].values()) == pytest.approx(1.0, abs=0.001)
    assert r["recommended_case"] == max(
        r["quadrant_probabilities"], key=r["quadrant_probabilities"].get
    )
    assert 0 <= r["prediction_confidence"] <= 100
    assert r["profile"] in ("Low", "Medium", "High")


def test_run_prediction_matches_underlying_functions():
    # The orchestration must not diverge from the tested building blocks.
    r = _default_prediction()
    assert r["control_class"] == m.classify_high_low(r["control_score"])
    assert r["recommended_case"] == m.recommended_case(r["quadrant_probabilities"])


def test_run_prediction_design_override_lowers_robustness_gate():
    # Advanced tuning: forcing a weak design (low SA + density) must be able to
    # push the entropy/robustness probability below the 50% design-quality gate.
    weak = m.run_prediction(
        age=9, al=24.5, myopia=-3.0, pupil=4.5,
        near_hours=5, outdoor_hours=1, csf_score=75, comfort_score=80,
        sa_strength=0.0, density=0,
    )
    assert weak["entropy_robustness_probability"] < 0.5


def test_run_followup_bands_and_boundary():
    # Stable / Monitor / Escalate bands.
    assert m.run_followup(24.50, 24.52, 12)["next_profile"] == "Medium"      # stable
    assert m.run_followup(24.50, 24.70, 3)["next_profile"] == "High"         # escalate
    # Exact 0.10 boundary must stay "maintain" despite float noise.
    r = m.run_followup(24.50, 24.55, 6)
    assert r["advice"] == "Maintain current NSO profile"
