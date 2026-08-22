"""
Every tunable constant in the NSO engine, in one place.

Rationale: none of these values came from clinical data (see ASSUMPTIONS.md).
Calibrating the engine must therefore be a *configuration* change, not a code
change -- otherwise every recalibration is a code review, a deploy, and a
chance to introduce a bug in logic that was working.

Two consequences follow from that:

  * Engine code reads ``CONFIG.<field>`` at call time, never a module-level
    literal, so a reload takes effect without touching the algorithms.
  * ``EngineConfig`` round-trips to JSON, so a calibrated parameter set is an
    artifact that can be versioned, diffed and shipped alongside a model.

``version`` identifies the parameter set. Bump it whenever a value changes;
predictions are stamped with it so a stored result can always be traced back
to the numbers that produced it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class EngineConfig:
    """Engine parameters. See ASSUMPTIONS.md for the provenance of each group."""

    version: str = "rule-1.0.0"

    # -- Clinical scales (ASSUMPTIONS P1-3, P1-4) --------------------------
    csf_bands: Dict[str, float] = field(
        default_factory=lambda: {"Low": 45.0, "Mid": 70.0, "High": 90.0}
    )
    csf_frequencies_cpd: Tuple[float, ...] = (1.5, 6.0, 18.0)

    # -- Binocular vision --------------------------------------------------
    #
    # Heterophoria is measured as a DEPARTURE FROM NORM, not from zero. Morgan's
    # norms put near phoria at about 3 exo and distance phoria near ortho, so a
    # patient who is orthophoric at near is already shifted esophorically. The
    # old index read only the exo half from zero, which made every esophore look
    # like a patient with no binocular finding at all.
    near_phoria_norm_d: float = -3.0      # prism dioptres, exo negative
    distance_phoria_norm_d: float = -1.0
    # Departure from norm, in prism dioptres, mapped onto 0-1.
    phoria_strain_span_d: float = 12.0
    # Distance phoria carries less weight than near: these lenses are worn for
    # sustained near work, which is where the vergence demand lives.
    distance_phoria_weight: float = 0.4

    # Stereoacuity, arc seconds, mapped onto 0-1 (normal is roughly 20-40").
    stereoacuity_normal_arcsec: float = 40.0
    stereoacuity_poor_arcsec: float = 400.0

    # Direction matters for the DESIGN, not only for the strain: a peripheral
    # add reduces accommodative convergence, which relieves an esophore at near
    # and worsens an exophore. So the same magnitude of phoria should push the
    # design in opposite directions depending on its sign.
    #
    # !! SET TO ZERO PENDING CLINICAL SIGN-OFF !!
    # The direction above is textbook; the magnitude is not something to invent.
    # The path is wired and tested so that entering a number here is the whole
    # change -- ASSUMPTIONS P1-18.
    sa_vergence_direction_gain: float = 0.0

    # -- Wavefront and corneal shape --------------------------------------
    #
    # The eye already has aberrations. A lens that ignores them is prescribing
    # into a vacuum: a patient with substantial positive corneal spherical
    # aberration does not need as much added, and one whose retinal image is
    # already degraded by coma or trefoil tolerates less extra optical load.
    #
    # Both directions are optics, not opinion. The MAGNITUDES are not something
    # to invent -- adding the wrong amount of SA is worse than adding none --
    # so every gain below ships at zero. The paths are wired and tested; filling
    # in a number is the whole change (ASSUMPTIONS P1-19).
    #
    # Reference corneal SA for an average eye, micrometres over a 6 mm pupil.
    corneal_sa_reference_um: float = 0.27
    # How much of the eye's own SA departure is subtracted from the lens SA.
    # 1.0 would mean full compensation.
    sa_corneal_compensation: float = 0.0
    # How much residual higher-order aberration caps the added optical load.
    sa_hoa_tolerance_gain: float = 0.0
    # Residual (lenticular) astigmatism = refractive cylinder - corneal
    # cylinder. It degrades the retinal image the design has to work with.
    hoa_residual_astigmatism_weight: float = 0.0
    # Corneal asphericity sets the eye's own peripheral defocus profile, which
    # the lens is meant to complement rather than duplicate.
    corneal_eccentricity_reference: float = 0.5
    sa_corneal_asphericity_gain: float = 0.0
    # Ocular dominance shifts the OD/OS split: the dominant eye may warrant the
    # gentler design. Direction plausible, magnitude unknown.
    dominance_asymmetry_gain: float = 0.0

    # -- Accommodative demand from working distance -----------------------
    # demand (D) = 100 / distance (cm) is definitional, not an assumption.
    # What IS an assumption is how much weight sustained demand carries in the
    # accommodative-stress index (ASSUMPTIONS P1-16).
    accommodative_demand_weight: float = 0.20
    # Demand range mapped onto 0-1: 2.0 D is a 50 cm desk, 5.0 D is 20 cm.
    accommodative_demand_min_d: float = 2.0
    accommodative_demand_max_d: float = 5.0
    # Assumed working distance when the clinician did not measure one.
    default_near_working_distance_cm: float = 40.0

    # -- Phenotype grading (P1-2) -----------------------------------------
    grade_low_ceiling: float = 40.0
    grade_mid_ceiling: float = 70.0

    # -- Candidate search (P3-4) ------------------------------------------
    #
    # Candidates vary on TWO axes, not one. With a single SA axis every option
    # traded the same way -- more control for less comfort -- so the goal
    # dropdown could only ever produce two answers: the strongest design or the
    # weakest. Density moves entropy and robustness on a different axis, which
    # is what lets a robustness-led goal pick something a comfort-led goal
    # would not.
    #
    # The grid is the cross product, so 3 x 3 = 9 candidates per eye and 81
    # pairs to score. ASSUMPTIONS P3-4: the step sizes are uncalibrated.
    candidate_offsets: Tuple[float, ...] = (-1.0, 0.0, 1.0)          # SA, dioptres
    candidate_density_offsets: Tuple[float, ...] = (-15.0, 0.0, 15.0)  # density, 0-100

    # -- Joint binocular optimization (P0-6) ------------------------------
    binocular_weight: float = 0.30
    sa_fusion_tolerance_d: float = 0.4
    density_fusion_tolerance: float = 12.0
    # Pair classification. mild_sa_ceiling is the boundary between "mildly
    # asymmetric" and "asymmetric"; the symmetric boundary reuses the fusion
    # tolerances above so the label and the optimizer cannot disagree (P2-4).
    mild_sa_ceiling: float = 1.2

    # -- Confidence (P1-11) ------------------------------------------------
    #
    # Confidence has two halves: how much was measured (coverage) and whether
    # what was measured looks like a patient the rules were written for
    # (plausibility). Coverage alone meant an out-of-range measurement raised
    # confidence exactly as much as a normal one -- the system grew more
    # certain as the patient grew more unusual.
    confidence_floor: float = 55.0
    confidence_coverage_span: float = 25.0
    confidence_plausibility_span: float = 20.0
    confidence_for_long_followup: float = 80.0

    # Ranges the rule-based engine was written against. A value outside its
    # range does not invalidate the fitting -- it means the engine is
    # extrapolating, and should say so by lowering confidence.
    #
    # ASSUMPTIONS P1-15: these are ordinary clinical ranges, not the training
    # range of a fitted model. When a model replaces the rules, replace these
    # with its actual training distribution.
    # Cost of one implausible measurement. Charged per violation rather than
    # as a share of all measurements: a physically impossible value is a red
    # flag on its own, and diluting it across a dozen normal readings would let
    # a thorough workup hide a transcription error.
    plausibility_penalty_per_violation: float = 0.25

    plausible_ranges: Dict[str, Tuple[float, float]] = field(
        default_factory=lambda: {
            "age": (6.0, 18.0),
            "axial_length": (21.5, 27.5),
            "spherical_equivalent": (-10.0, 2.0),
            "photopic_pupil": (2.5, 7.5),
            "near_phoria": (-15.0, 10.0),
            "npc": (2.0, 20.0),
            "accommodative_lag": (0.0, 2.5),
            "near_hours": (0.0, 14.0),
            "digital_hours": (0.0, 14.0),
            "outdoor_hours": (0.0, 8.0),
            "visual_stress_score": (0.0, 10.0),
            "pfv": (5.0, 40.0),
            "nfv": (3.0, 30.0),
            "ac_a": (0.0, 12.0),
            "stereoacuity": (10.0, 400.0),
            "amplitude_of_accommodation": (2.0, 20.0),
            "accommodative_facility": (0.0, 20.0),
            "mesopic_pupil": (3.0, 9.0),
            "near_working_distance": (15.0, 60.0),
            "computer_working_distance": (30.0, 90.0),
            "typical_working_distance": (15.0, 90.0),
            "csf_low": (0.0, 100.0),
            "csf_mid": (0.0, 100.0),
            "csf_high": (0.0, 100.0),
            "hoa_rms": (0.0, 1.0),
            "corneal_sa": (-0.5, 0.8),
            "coma": (0.0, 0.8),
            "trefoil": (0.0, 0.8),
            "corneal_astigmatism": (0.0, 6.0),
            "corneal_eccentricity": (0.0, 1.2),
            "vep": (0.0, 5.0),
            "erg": (0.0, 5.0),
            "eye_tracking": (0.0, 5.0),
        }
    )

    # -- Closed-loop refitting (P1-6) --------------------------------------
    refit_escalation: Dict[str, float] = field(
        default_factory=lambda: {
            "Controlled": 0.0, "Borderline": 0.35, "Progressing": 0.80
        }
    )
    progression_controlled_ceiling: float = 0.10
    progression_borderline_ceiling: float = 0.20

    # -- Lens construction (P0-1) — PLACEHOLDERS, NOT THE REAL PRODUCT -----
    lens_index: float = 1.60
    optic_zone_diameter_mm: float = 40.0
    base_curve_d: float = 4.0
    sa_reference_semi_diameter_mm: float = 10.0
    # Width over which the higher-order term blends into its plateau. The
    # blend replaces a hard clamp that was C0- but not C2-continuous, i.e. it
    # put a curvature step -- an optical defect ring -- at the reference
    # radius. ASSUMPTIONS P0-3: a smooth taper is strictly better than a step,
    # but WHICH taper the real design uses is still unspecified.
    sa_rolloff_blend_mm: float = 4.0

    # -- Geometry sampling (P2-9) ------------------------------------------
    surface_radial_samples: int = 41
    surface_meridional_samples: int = 24
    microstructure_page_size: int = 2000

    # -- Acceptance windows (P0-4) — PLACEHOLDERS --------------------------
    sag_tolerance_mm: float = 0.002
    height_tolerance_mm: float = 0.0002
    position_tolerance_mm: float = 0.005
    decentration_tolerance_mm: float = 0.25

    # -- Design synthesis coefficients (P1-5) ------------------------------
    # The phenotype -> optical design mapping. This is the commercially
    # valuable part of the system and the least evidenced part of it.
    sa_accommodative_gain: float = 0.12
    sa_stress_gain: float = -0.18
    sa_asymmetry_gain: float = 0.90
    sa_progression_gain: float = 0.25
    sa_min_d: float = 1.5
    sa_max_d: float = 9.0

    density_stress_gain: float = -0.20
    density_binocular_gain: float = 0.10
    density_asymmetry_gain: float = 0.50
    density_min: float = 20.0
    density_max: float = 100.0

    element_diameter_base_um: float = 22.0
    element_diameter_pupil_gain_um: float = 10.0
    element_diameter_density_gain_um: float = 0.04
    element_height_sa_gain_um: float = 0.22
    element_height_csf_gain_um: float = 0.6
    element_height_base_um: float = 0.9
    fill_factor_base_pct: float = 20.0
    fill_factor_density_gain: float = 0.28
    fill_factor_csf_gain: float = -8.0
    jitter_base_deg: float = 4.0
    jitter_csf_gain_deg: float = 8.0
    jitter_stress_gain_deg: float = 2.0
    temporal_binocular_gain: float = 0.06
    target_mtf_ceiling: float = 0.85
    target_mtf_csf_gain: float = -0.08
    target_mtf_floor: float = 0.35
    # Optical load costs acuity: MTF falls as the design's SA departs upward
    # from its tier's nominal value. Without this term every candidate in a
    # tier reports the same acuity and the candidate comparison is inert.
    # ASSUMPTIONS P1-14: the slope is uncalibrated.
    target_mtf_sa_gain: float = -0.02      # per dioptre above tier nominal
    zone_count: int = 3

    # -- Loss weights and hard constraints (P1-12) -------------------------
    #
    # The loss encodes what we VALUE, separately from what a predictor says
    # WILL HAPPEN. Keeping the two apart means a new model changes the forecast
    # without silently changing clinical priorities.
    loss_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "control": 0.35, "acuity": 0.20, "comfort": 0.15,
            "adaptation": 0.15, "robustness": 0.10, "manufacturability": 0.05,
        }
    )

    # Per-goal weight overrides. The clinician's "Primary Optimization Goal" is
    # exactly a statement about priorities, so this is where it belongs -- it
    # re-ranks the same candidates rather than generating different ones.
    #
    # ASSUMPTIONS P1-13: the ordering is defensible (night vision cares about
    # acuity and pupil-size robustness; digital comfort cares about comfort and
    # adaptation) but the magnitudes are uncalibrated, like every other weight
    # here. Each row must sum to 1.0.
    goal_loss_weights: Dict[str, Dict[str, float]] = field(
        default_factory=lambda: {
            # Myopia control is never optional: this is a myopia-control lens,
            # and a child wearing it needs control whatever else they need. So
            # every profile keeps a substantial control weight, and the goal
            # decides what gets traded AROUND it -- and, given the two design
            # axes, HOW control is bought: extra SA costs acuity, extra
            # coverage costs comfort and robustness.
            #
            # An earlier table let control fall to 0.10 for most goals, which
            # made every non-myopia goal pick the weakest design in the grid
            # and collapsed nine goals into two answers.
            "Myopia Management": {
                "control": 0.45, "acuity": 0.15, "comfort": 0.13,
                "adaptation": 0.12, "robustness": 0.10, "manufacturability": 0.05},
            "Digital Visual Comfort": {
                "control": 0.25, "acuity": 0.12, "comfort": 0.30,
                "adaptation": 0.23, "robustness": 0.05, "manufacturability": 0.05},
            "Reading": {
                "control": 0.25, "acuity": 0.25, "comfort": 0.22,
                "adaptation": 0.18, "robustness": 0.05, "manufacturability": 0.05},
            "Near Work": {
                "control": 0.25, "acuity": 0.15, "comfort": 0.25,
                "adaptation": 0.25, "robustness": 0.05, "manufacturability": 0.05},
            "Presbyopia": {
                "control": 0.10, "acuity": 0.40, "comfort": 0.22,
                "adaptation": 0.18, "robustness": 0.05, "manufacturability": 0.05},
            "Driving": {
                "control": 0.25, "acuity": 0.35, "comfort": 0.10,
                "adaptation": 0.08, "robustness": 0.17, "manufacturability": 0.05},
            "Night Vision": {
                "control": 0.25, "acuity": 0.38, "comfort": 0.10,
                "adaptation": 0.07, "robustness": 0.15, "manufacturability": 0.05},
            "Sports Vision": {
                "control": 0.25, "acuity": 0.20, "comfort": 0.08,
                "adaptation": 0.12, "robustness": 0.30, "manufacturability": 0.05},
            "General Visual Comfort": {
                "control": 0.20, "acuity": 0.15, "comfort": 0.30,
                "adaptation": 0.25, "robustness": 0.05, "manufacturability": 0.05},
        }
    )

    def weights_for_goal(self, goal: str) -> Dict[str, float]:
        """Loss weights for a primary optimization goal.

        An unrecognised goal falls back to the default weights rather than
        raising: a new dropdown entry should degrade to sensible behaviour, not
        take the fitting endpoint down.
        """
        return self.goal_loss_weights.get(goal, self.loss_weights)
    # Optical load buys control: predicted myopia control scales with how far
    # the design's SA sits from its tier's nominal value. This is the premise
    # of the technology, so it must be in the model -- without it SA can be
    # raised at no predicted benefit, every candidate reports the same control,
    # and the optimizer always picks the weakest design.
    #
    # 1.0 means simple linear proportionality: a design carrying 20% more SA
    # than its tier's nominal is predicted to control 20% more. That is a
    # statable assumption rather than a tuned number, but it is still
    # uncalibrated -- ASSUMPTIONS P1-14.
    control_sa_sensitivity: float = 1.0

    # Treated area buys control too, and on a different axis than SA: fill
    # factor is the fraction of the lens carrying the defocus signal, so more
    # of it means more treatment for the same optical strength. This is the
    # coverage premise the whole lens class rests on.
    #
    # Without it fill factor had costs and no benefit, so the optimizer drove
    # it to the floor and the second candidate axis was inert -- the same
    # failure SA had before control_sa_sensitivity existed.
    #
    # 1.0 is linear proportionality against the reference coverage.
    # ASSUMPTIONS P1-14: uncalibrated.
    control_fill_factor_sensitivity: float = 1.0
    reference_fill_factor_pct: float = 35.0

    # Robustness is a property of the patient AND the design: a busier surface
    # tolerates decentration and pupil variation less well. Without this term
    # the robustness weight is inert -- every candidate scores the patient's
    # index and weighting robustness changes nothing. ASSUMPTIONS P1-14.
    robustness_entropy_penalty: float = 0.25

    # Augmentation term for the Tchebycheff scalarization. Small: it only
    # breaks ties among designs with the same worst-case gap, in favour of the
    # one that is better overall.
    scalarization_augmentation: float = 0.05

    min_acuity: float = 40.0
    min_adaptation: float = 35.0
    min_manufacturability: float = 30.0

    # -- Manufacturing job identity (P3-6) ---------------------------------
    default_site: str = "SG"
    job_serial_start: int = 8721

    # ------------------------------------------------------------------ io
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EngineConfig":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config fields: {sorted(unknown)}")
        # JSON has no tuples, so a round-trip would otherwise turn every tuple
        # field into a list and stop comparing equal to the config that wrote
        # it. Restore by inspecting the declared defaults rather than listing
        # field names, so a new tuple field cannot forget to be handled.
        data = dict(data)
        reference = cls()
        for name, value in list(data.items()):
            default = getattr(reference, name, None)
            if isinstance(default, tuple) and isinstance(value, list):
                data[name] = tuple(value)
            elif isinstance(default, dict) and isinstance(value, dict):
                data[name] = {
                    k: tuple(v) if isinstance(v, list) else v
                    for k, v in value.items()
                }
        return cls(**data)

    @classmethod
    def load(cls, path: str | Path) -> "EngineConfig":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def evolve(self, **changes: Any) -> "EngineConfig":
        """A copy with fields replaced — used by calibration sweeps and tests."""
        return replace(self, **changes)


# The active parameter set. Swap it with ``use_config`` rather than mutating it;
# EngineConfig is frozen so a stored prediction's config version stays true.
CONFIG = EngineConfig()


def use_config(config: EngineConfig) -> EngineConfig:
    """Install a parameter set globally. Returns the previous one."""
    global CONFIG
    previous, CONFIG = CONFIG, config
    return previous


def get_config() -> EngineConfig:
    return CONFIG
