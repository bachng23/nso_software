"""
Feature extraction — the seam where a trained model will plug in.

Everything a predictor is allowed to see about a patient or a design goes
through a :class:`FeatureVector`. That matters more than it looks:

  * **Train/serve parity.** Training data and live requests are built by the
    same function, so a model cannot be trained on features that production
    computes differently.
  * **Schema versioning.** ``SCHEMA_VERSION`` changes whenever the feature set
    changes. A stored model records which schema it was trained against, and
    loading it against a different one is an error rather than silent garbage.
  * **A training-data format that exists today.** ``training_row`` emits a flat
    dict per (patient, design) pair. Logging those from day one is what makes a
    model possible later; features invented at training time are the usual
    reason an ML project stalls.

Two vectors, because the thing to be learned is ``outcome = f(patient, design)``:

  * :func:`patient_features` — clinical, safe to log and to expose in aggregate.
  * :func:`design_features` — Design IP. Never leaves the server, never appears
    in a clinical payload, and is excluded from anything a vendor receives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import nso_core as engine
from nso_core import norm

from .config import get_config
from .patient import PatientInput
from .recipe import DesignRecipe


def clip100(x: float) -> float:
    return float(max(0.0, min(float(x), 100.0)))


# --------------------------------------------------------------------------- #
# AI-derived indices (0-100). Rule-based, deterministic, not calibrated.
# See ASSUMPTIONS.md P1-1: every weight below is a placeholder.
# --------------------------------------------------------------------------- #

def refractive_risk_index(p: PatientInput) -> float:
    al = max(p.od.axial_length, p.os.axial_length)
    se = min(p.od.spherical_equivalent, p.os.spherical_equivalent)
    raw = (
        0.35 * norm(al, 22.5, 26.5)
        + 0.30 * norm(abs(se), 1.0, 8.0)
        + 0.20 * (1 - norm(p.age, 6, 18))
        + 0.15 * norm(p.near_hours, 1, 12)
    )
    return round(clip100(raw * 100), 1)


def phoria_departure(phoria_d: float, norm_d: float) -> float:
    """Signed departure from the phoria norm, prism dioptres.

    Positive = shifted esophoric relative to norm, negative = exophoric. Both
    directions are findings; zero is not the reference point (Morgan's near norm
    is about 3 exo).
    """
    return phoria_d - norm_d


def sheard_deficit(phoria_d: float, norm_d: float, reserve_d: Optional[float]) -> Optional[float]:
    """Shortfall against Sheard's criterion, prism dioptres.

    Sheard: the fusional reserve OPPOSING the deviation should be at least twice
    the phoria. Which reserve opposes it depends on direction -- an exophore is
    held by positive fusional vergence, an esophore by negative -- so the caller
    passes whichever applies. Returns None when that reserve was not measured.

    This is a textbook criterion rather than an invented weighting, which is why
    it is used directly instead of being hidden behind a tunable coefficient.
    """
    if reserve_d is None:
        return None
    return max(0.0, 2.0 * abs(phoria_departure(phoria_d, norm_d)) - reserve_d)


def binocular_load_index(p: PatientInput) -> float:
    """Higher = more binocular strain.

    Strain is departure from norm in EITHER direction, plus a receded NPC, plus
    any shortfall in the reserve that opposes the deviation.
    """
    cfg = get_config()

    near_departure = phoria_departure(p.near_phoria, cfg.near_phoria_norm_d)
    raw = 0.40 * norm(abs(near_departure), 0, cfg.phoria_strain_span_d)
    raw += 0.30 * norm(p.npc, 5, 15)
    weight = 0.70

    if p.distance_phoria is not None:
        distance_departure = phoria_departure(
            p.distance_phoria, cfg.distance_phoria_norm_d)
        w = 0.20 * cfg.distance_phoria_weight
        raw += w * norm(abs(distance_departure), 0, cfg.phoria_strain_span_d)
        weight += w

    # An esophore is compensated by negative fusional vergence, an exophore by
    # positive. Reading the wrong one would call a well-compensated patient
    # strained.
    opposing_reserve = p.nfv if near_departure > 0 else p.pfv
    deficit = sheard_deficit(p.near_phoria, cfg.near_phoria_norm_d, opposing_reserve)
    if deficit is not None:
        raw += 0.20 * norm(deficit, 0, 15)
        weight += 0.20

    if p.ac_a is not None:
        raw += 0.15 * norm(abs(p.ac_a - 4.0), 0, 6)
        weight += 0.15

    if p.stereoacuity is not None:
        raw += 0.10 * norm(
            p.stereoacuity, cfg.stereoacuity_normal_arcsec, cfg.stereoacuity_poor_arcsec)
        weight += 0.10

    return round(clip100(raw / weight * 100), 1)


def accommodative_demand_d(p: PatientInput) -> Optional[float]:
    """Accommodative demand in dioptres from the measured working distances.

    ``demand = 100 / distance_cm`` is the definition of dioptric demand, not a
    modelling choice. When both a near and a computer distance are recorded the
    nearer one governs, because that is the harder task the eye sustains.

    Returns None when neither distance was measured, so the caller can tell
    "not measured" from "measured and comfortable".
    """
    distances = [d for d in (p.near_working_distance, p.computer_working_distance)
                 if d is not None and d > 0]
    if not distances:
        return None
    return round(100.0 / min(distances), 2)


def accommodative_stress_index(p: PatientInput) -> float:
    cfg = get_config()
    raw = 0.45 * norm(p.accommodative_lag, 0.25, 2.0) + 0.30 * norm(
        p.near_hours, 2, 14
    )
    weight = 0.75

    demand = accommodative_demand_d(p)
    if demand is not None:
        raw += cfg.accommodative_demand_weight * norm(
            demand, cfg.accommodative_demand_min_d, cfg.accommodative_demand_max_d
        )
        weight += cfg.accommodative_demand_weight

    if p.amplitude_of_accommodation is not None:
        expected = max(4.0, 18.5 - 0.30 * p.age)   # Hofstetter minimum
        raw += 0.15 * norm(expected - p.amplitude_of_accommodation, 0, 6)
        weight += 0.15
    if p.accommodative_facility is not None:
        raw += 0.10 * (1 - norm(p.accommodative_facility, 3, 12))
        weight += 0.10
    return round(clip100(raw / weight * 100), 1)


def spatial_frequency_sensitivity_index(p: PatientInput) -> float:
    """Higher = better spatial-frequency performance (this one is a quality).

    The roll-off penalty reads the measurement's SLOPE rather than the
    difference between two raw values. Slope is per decade of spatial
    frequency, so it accounts for the frequencies the instrument actually
    used: losing 0.8 logCS between 1.5 and 18 cpd is a gentler curve than
    losing the same amount between 1.5 and 6, and a difference of raw values
    cannot tell those apart.
    """
    base = p.csf_value()
    measurement = p.csf()
    if measurement is not None:
        slope = measurement.slope()
        if slope is not None and slope < 0:
            # A steep high-frequency roll-off costs more than the mean suggests.
            base = base * (1 - 0.20 * norm(-slope, 0.0, 1.5))
    return round(clip100(base), 1)


def visual_stress_index(p: PatientInput) -> float:
    digital_fraction = min(1.0, p.digital_hours / max(p.near_hours, 0.25))
    raw = 0.50 * norm(p.visual_stress_score, 0, 10) + 0.25 * digital_fraction
    weight = 0.75
    raw += 0.15 * norm(p.photopic_pupil, 3.0, 6.5)
    weight += 0.15
    if p.low_light_demand == "High" or p.night_driving:
        raw += 0.10
        weight += 0.10
    return round(clip100(raw / weight * 100), 1)


def neural_adaptation_index(p: PatientInput) -> float:
    if p.neural_adaptation_score is not None:
        base = p.neural_adaptation_score * 10.0
    else:
        base = engine.neural_adaptation(
            p.age, p.comfort_value(), p.csf_value(), entropy=50.0
        )
    youth_bonus = 8.0 * (1 - norm(p.age, 6, 18))
    # An eye whose retinal image is already degraded adapts to added optical
    # load less readily.
    aberration_penalty = 15.0 * residual_aberration_load(p)
    return round(clip100(base + youth_bonus - aberration_penalty), 1)


def dynamic_robustness_index(p: PatientInput) -> float:
    stability = (
        p.dynamic_visual_stability * 10.0
        if p.dynamic_visual_stability is not None
        else 70.0
    )
    if p.mesopic_pupil is not None:
        pupil_penalty = norm(p.mesopic_pupil - p.photopic_pupil, 0.5, 3.5) * 100
    else:
        pupil_penalty = norm(p.photopic_pupil, 3.0, 6.5) * 100
    raw = 0.55 * stability + 0.25 * (100 - pupil_penalty) + 0.20 * p.csf_value()
    return round(clip100(raw), 1)


def interocular_image_balance_index(p: PatientInput) -> float:
    """Higher = better balanced. Anisometropia and AL asymmetry reduce it."""
    aniso = abs(p.od.spherical_equivalent - p.os.spherical_equivalent)
    al_diff = abs(p.od.axial_length - p.os.axial_length)
    penalty = 0.55 * norm(aniso, 0.25, 3.0) + 0.45 * norm(al_diff, 0.1, 1.5)

    va_diff = interocular_acuity_difference(p)
    if va_diff is not None:
        penalty = 0.80 * penalty + 0.20 * norm(va_diff, 0.0, 0.3)

    # Clinician-graded binocular balance overrides a clean numeric picture: a
    # patient can be isometropic and still fuse poorly.
    penalty = max(penalty, {"Normal": 0.0, "Mild": 0.25, "Significant": 0.55}
                  .get(p.binocular_balance, 0.0))
    return round(clip100(100 - penalty * 100), 1)


def task_load_index(p: PatientInput) -> float:
    raw = (
        0.40 * norm(p.near_hours, 2, 14)
        + 0.30 * (1 - norm(p.outdoor_hours, 0, 3))
        + 0.20 * (1 - norm(
            p.typical_working_distance
            or p.near_working_distance
            or get_config().default_near_working_distance_cm,
            25, 60))
    )
    weight = 0.90
    if p.night_driving or p.low_light_demand == "High":
        raw += 0.10
        weight += 0.10
    return round(clip100(raw / weight * 100), 1)


def ai_derived_indices(p: PatientInput) -> Dict[str, float]:
    """The eight indices shown to clinicians (Section: AI-Derived Visual Indices)."""
    return {
        "refractive_risk": refractive_risk_index(p),
        "binocular_load": binocular_load_index(p),
        "accommodative_stress": accommodative_stress_index(p),
        "spatial_frequency_sensitivity": spatial_frequency_sensitivity_index(p),
        "visual_stress": visual_stress_index(p),
        "neural_adaptation": neural_adaptation_index(p),
        "dynamic_robustness": dynamic_robustness_index(p),
        "interocular_image_balance": interocular_image_balance_index(p),
    }


# --------------------------------------------------------------------------- #
# Section 4 auto-calculated descriptors.
#
# CAVEAT (ASSUMPTIONS P1-4): real CSF is measured at named spatial frequencies.
# Without the acquisition protocol the three samples sit at the nominal
# frequencies in ``CONFIG.csf_frequencies_cpd``.
# --------------------------------------------------------------------------- #

def csf_descriptors(p: PatientInput) -> Dict[str, Optional[float]]:
    """AUC, slope and sensitivity centroid from the measurement.

    Computed at the frequencies the instrument actually used, so a clinic with
    a different protocol gets correct numbers rather than numbers computed
    against an assumption the engine made on its behalf.
    """
    measurement = p.csf()
    if measurement is None:
        return {"csf_auc": None, "csf_slope": None, "csf_centroid_cpd": None}
    return measurement.descriptors()


def interocular_acuity_difference(p: PatientInput) -> Optional[float]:
    """Section 2 'Interocular Visual Acuity Difference | calculated'."""
    if p.od.bcva_logmar is None or p.os.bcva_logmar is None:
        return None
    return round(abs(p.od.bcva_logmar - p.os.bcva_logmar), 3)


def _measured_values(p: PatientInput) -> Dict[str, float]:
    """Every measurement that has a plausibility range, where one was taken."""
    values: Dict[str, Optional[float]] = {
        "age": p.age,
        "photopic_pupil": p.photopic_pupil,
        "near_phoria": p.near_phoria,
        "npc": p.npc,
        "accommodative_lag": p.accommodative_lag,
        "near_hours": p.near_hours,
        "digital_hours": p.digital_hours,
        "outdoor_hours": p.outdoor_hours,
        "visual_stress_score": p.visual_stress_score,
        "pfv": p.pfv, "nfv": p.nfv, "ac_a": p.ac_a,
        "stereoacuity": p.stereoacuity,
        "amplitude_of_accommodation": p.amplitude_of_accommodation,
        "accommodative_facility": p.accommodative_facility,
        "mesopic_pupil": p.mesopic_pupil,
        "near_working_distance": p.near_working_distance,
        "computer_working_distance": p.computer_working_distance,
        "typical_working_distance": p.typical_working_distance,
        "csf_low": p.csf_low, "csf_mid": p.csf_mid, "csf_high": p.csf_high,
        "hoa_rms": p.hoa_rms, "corneal_sa": p.corneal_sa,
        "coma": p.coma, "trefoil": p.trefoil,
        "corneal_astigmatism": p.corneal_astigmatism,
        "corneal_eccentricity": p.corneal_eccentricity,
        "vep": p.vep, "erg": p.erg, "eye_tracking": p.eye_tracking,
    }
    taken = {k: float(v) for k, v in values.items() if v is not None}
    # Both eyes are checked against the same per-eye ranges.
    for label, eye in (("od", p.od), ("os", p.os)):
        taken[f"axial_length_{label}"] = eye.axial_length
        taken[f"spherical_equivalent_{label}"] = eye.spherical_equivalent
    return taken


def out_of_range_measurements(p: PatientInput) -> List[Dict[str, Any]]:
    """Measurements outside the range the rules were written for.

    Not errors: the fitting still runs. They mean the engine is extrapolating,
    which is a reason to be less confident rather than more -- the failure this
    replaces counted an implausible measurement as evidence.
    """
    ranges = get_config().plausible_ranges
    flagged = []
    for name, value in _measured_values(p).items():
        # Per-eye keys share one range, e.g. axial_length_od -> axial_length.
        base = name.rsplit("_", 1)[0] if name.endswith(("_od", "_os")) else name
        bounds = ranges.get(base)
        if bounds is None:
            continue
        low, high = bounds
        if value < low or value > high:
            flagged.append({
                "measurement": name,
                "value": round(value, 3),
                "expected_low": low,
                "expected_high": high,
            })
    return sorted(flagged, key=lambda f: f["measurement"])


def plausibility(p: PatientInput) -> float:
    """1.0 when every measurement is in range, falling with each that is not.

    Charged per violation, not as a proportion: one impossible reading is a red
    flag whether it sits among three measurements or thirty, and a proportional
    score would let a thorough workup bury a transcription error.
    """
    penalty = get_config().plausibility_penalty_per_violation
    return max(0.0, 1.0 - penalty * len(out_of_range_measurements(p)))


# Which optional domain each measurement belongs to, so an implausible reading
# can be excluded from the domain it claims to cover.
DOMAIN_MEASUREMENTS = {
    "binocular_extended": ("pfv", "nfv", "ac_a", "stereoacuity"),
    "accommodation_extended": ("amplitude_of_accommodation", "accommodative_facility"),
    "csf_measured": ("csf_low", "csf_mid", "csf_high"),
    "neurovisual_extended": ("vep", "erg", "eye_tracking"),
    "wavefront": ("hoa_rms", "corneal_sa", "coma", "trefoil"),
}


def plausibly_measured_domains(p: PatientInput) -> Dict[str, bool]:
    """Measured domains, minus any whose readings are out of range.

    A measurement outside its plausible range has told us nothing reliable
    about that domain, so it should not count as coverage. Without this an
    implausible reading raised coverage and lowered plausibility by the same
    amount and the two cancelled -- recording nonsense came out neutral.
    """
    flagged = {f["measurement"] for f in out_of_range_measurements(p)}
    domains = p.measured_domains()
    return {
        domain: measured and not (flagged & set(DOMAIN_MEASUREMENTS.get(domain, ())))
        for domain, measured in domains.items()
    }


def corneal_sa_departure(p: PatientInput) -> float:
    """The eye's own spherical aberration, relative to an average eye (um).

    Positive means the patient already carries more positive SA than typical,
    so less needs to be added. Zero when not measured -- which is the honest
    default: an unmeasured eye is assumed average, not assumed to need
    compensation.
    """
    if p.corneal_sa is None:
        return 0.0
    return float(p.corneal_sa) - get_config().corneal_sa_reference_um


def residual_astigmatism(p: PatientInput) -> Optional[float]:
    """Refractive cylinder not explained by the cornea, dioptres.

    ``residual = |refractive cylinder| - corneal astigmatism`` is definitional.
    What it means for the design -- a lenticular component the spectacle plane
    corrects differently -- is where the judgement starts.
    """
    if p.corneal_astigmatism is None:
        return None
    refractive_cyl = max(abs(p.od.cylinder), abs(p.os.cylinder))
    return round(refractive_cyl - float(p.corneal_astigmatism), 3)


def residual_aberration_load(p: PatientInput) -> float:
    """0-1 summary of the aberration the eye brings to the fitting.

    Higher means a retinal image already degraded before the lens is added, so
    less headroom for extra optical load. Coma and trefoil are included because
    neither is corrected by a sphero-cylindrical prescription.
    """
    cfg = get_config()
    terms, weights = [], []
    if p.hoa_rms is not None:
        terms.append(norm(p.hoa_rms, 0.1, 0.6)); weights.append(0.5)
    if p.coma is not None:
        terms.append(norm(abs(p.coma), 0.05, 0.5)); weights.append(0.25)
    if p.trefoil is not None:
        terms.append(norm(abs(p.trefoil), 0.05, 0.5)); weights.append(0.25)

    residual = residual_astigmatism(p)
    if residual is not None:
        terms.append(norm(abs(residual), 0.25, 2.0))
        weights.append(cfg.hoa_residual_astigmatism_weight or 0.25)

    if not terms:
        return 0.0
    return float(sum(t * w for t, w in zip(terms, weights)) / sum(weights))


def corneal_asphericity_departure(p: PatientInput) -> float:
    """Corneal shape factor relative to an average cornea.

    A more prolate cornea already produces relative peripheral hyperopia, which
    is the signal the lens is trying to create. Zero when not measured.
    """
    if p.corneal_eccentricity is None:
        return 0.0
    return float(p.corneal_eccentricity) - get_config().corneal_eccentricity_reference


def dominance_bias(p: PatientInput, eye_key: str) -> float:
    """-1 .. +1: how much this eye is favoured for a gentler design.

    The dominant eye carries more of the visual task, so it may warrant the
    less disruptive of the two designs. Direction is plausible; the coefficient
    that scales it ships at zero.
    """
    if p.ocular_dominance == eye_key:
        return 1.0
    if p.ocular_dominance in ("OD", "OS"):
        return -1.0
    return 0.0


def vergence_direction(p: PatientInput) -> float:
    """-1 (exophoric) .. +1 (esophoric), relative to the near norm.

    Magnitude alone cannot tell the design what to do: a peripheral add relieves
    an esophore at near and burdens an exophore, so the same 6-dioptre departure
    should move the design opposite ways depending on its sign. This exposes the
    sign for the synthesis step; see CONFIG.sa_vergence_direction_gain for why
    its coefficient is currently zero.
    """
    cfg = get_config()
    departure = phoria_departure(p.near_phoria, cfg.near_phoria_norm_d)
    return float(max(-1.0, min(departure / cfg.phoria_strain_span_d, 1.0)))


def eye_risk(eye) -> float:
    """Per-eye refractive load, 0-1. Drives OD/OS design asymmetry."""
    return 0.55 * norm(eye.axial_length, 22.5, 26.5) + 0.45 * norm(
        abs(eye.spherical_equivalent), 1.0, 8.0
    )


# --------------------------------------------------------------------------- #
# The ML seam
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FeatureVector:
    """An ordered, named, versioned set of model inputs.

    Ordering is fixed by ``names`` so ``to_array`` is stable across processes —
    a model trained on position 7 keeps reading position 7.
    """

    SCHEMA_VERSION = "1.0.0"

    kind: str                    # "patient" or "design"
    values: Dict[str, float]

    def names(self) -> Tuple[str, ...]:
        return tuple(sorted(self.values))

    def to_array(self) -> List[float]:
        return [float(self.values[n]) for n in self.names()]

    def prefixed(self) -> Dict[str, float]:
        """Flat dict with the kind as a prefix, for joining the two vectors."""
        return {f"{self.kind}__{k}": v for k, v in self.values.items()}

    def __len__(self) -> int:
        return len(self.values)


# Missing optional measurements are imputed to a neutral value AND flagged with
# a companion ``*_measured`` indicator. A model must be able to tell "average"
# from "not measured"; collapsing the two is a classic silent bias.
_NEUTRAL = 0.0


def _opt(value: Optional[float], neutral: float = _NEUTRAL):
    return (neutral, 0.0) if value is None else (float(value), 1.0)


def patient_features(p: PatientInput) -> FeatureVector:
    """Clinical features. Safe to log; contains no design information."""
    idx = ai_derived_indices(p)
    csf = csf_descriptors(p)

    values: Dict[str, float] = {
        # Raw clinical measurements
        "age": float(p.age),
        "od_se": p.od.spherical_equivalent,
        "os_se": p.os.spherical_equivalent,
        "od_axial_length": float(p.od.axial_length),
        "os_axial_length": float(p.os.axial_length),
        "photopic_pupil": float(p.photopic_pupil),
        "near_phoria": float(p.near_phoria),
        "npc": float(p.npc),
        "accommodative_lag": float(p.accommodative_lag),
        "visual_stress_score": float(p.visual_stress_score),
        "near_hours": float(p.near_hours),
        "digital_hours": float(p.digital_hours),
        "outdoor_hours": float(p.outdoor_hours),
        "night_driving": 1.0 if p.night_driving else 0.0,
        # Resolved scales the engine actually consumes. Held as features in
        # their own right so a predictor never needs the PatientInput object.
        "comfort_value": p.comfort_value(),
        "csf_value": p.csf_value(),
        # Derived indices
        **{f"index_{k}": v for k, v in idx.items()},
        "index_task_load": task_load_index(p),
        # Closed-loop state
        "progression_load": float(p.progression_load),
        # Signed, so a model can learn the direction effect the rules currently
        # leave at zero.
        "vergence_direction": vergence_direction(p),
        "corneal_sa_departure": corneal_sa_departure(p),
        "corneal_asphericity_departure": corneal_asphericity_departure(p),
        "residual_aberration_load": residual_aberration_load(p),
        "dominance_od": dominance_bias(p, "OD"),
    }

    # Optional measurements, each with a measured/not-measured indicator.
    optional = {
        "mesopic_pupil": p.mesopic_pupil,
        "pfv": p.pfv,
        "nfv": p.nfv,
        "ac_a": p.ac_a,
        "stereoacuity": p.stereoacuity,
        "amplitude_of_accommodation": p.amplitude_of_accommodation,
        "accommodative_facility": p.accommodative_facility,
        "csf_low": p.csf_low,
        "csf_mid": p.csf_mid,
        "csf_high": p.csf_high,
        "csf_auc": csf["csf_auc"],
        "csf_slope": csf["csf_slope"],
        "csf_centroid_cpd": csf["csf_centroid_cpd"],
        "vep": p.vep,
        "erg": p.erg,
        "eye_tracking": p.eye_tracking,
        # Structured neurovisual. Recorded and exposed as features now, so the
        # dataset exists when these are ready to enter the optimizer -- which
        # the V2.1 baseline expects eye tracking to do first.
        "vep_amplitude_uv": p.vep_amplitude_uv,
        "vep_latency_ms": p.vep_latency_ms,
        "vep_interocular_difference_ms": p.vep_interocular_difference_ms,
        "vep_z_score": p.vep_z_score,
        "erg_z_score": p.erg_z_score,
        "fixation_stability": p.fixation_stability,
        "blink_rate": p.blink_rate,
        "vergence_stability": p.vergence_stability,
        "pupil_dynamics": p.pupil_dynamics,
        "gaze_distribution": p.gaze_distribution,
        "hoa_rms": p.hoa_rms,
        "corneal_sa": p.corneal_sa,
        "coma": p.coma,
        "trefoil": p.trefoil,
        "corneal_astigmatism": p.corneal_astigmatism,
        "corneal_eccentricity": p.corneal_eccentricity,
        "residual_astigmatism": residual_astigmatism(p),
        "interocular_acuity_difference": interocular_acuity_difference(p),
    }
    for name, raw in optional.items():
        value, present = _opt(raw)
        values[name] = value
        values[f"{name}_measured"] = present

    # Ordered categoricals, encoded rather than one-hot to keep the vector small.
    values["low_light_demand"] = {"Low": 0.0, "Moderate": 0.5, "High": 1.0}.get(
        p.low_light_demand, 0.5)
    values["binocular_balance"] = {"Normal": 0.0, "Mild": 0.5, "Significant": 1.0}.get(
        p.binocular_balance, 0.0)
    values["ocular_dominance_od"] = 1.0 if p.ocular_dominance == "OD" else 0.0
    values["ocular_dominance_os"] = 1.0 if p.ocular_dominance == "OS" else 0.0

    return FeatureVector(kind="patient", values=values)


def _effective_control_power(recipe: DesignRecipe) -> float:
    """Tier control power scaled by this design's optical load.

    Two independent contributions, matching the two candidate axes:

      * **strength** -- the NSO peak zone target relative to the tier nominal
      * **coverage** -- mean fill factor relative to the reference coverage

    Both raise predicted control, and each costs something different (a higher
    target costs acuity, more coverage costs comfort and robustness). That is
    what makes the two axes a real choice rather than one dominated ranking.
    """
    cfg = get_config()
    profile = engine.NSO_PROFILES[recipe.profile_tier]

    nominal = profile["sa_strength"] or 1.0
    strength_ratio = recipe.nso_peak_target_d / nominal - 1.0
    coverage_ratio = recipe.mean_fill_factor_pct / cfg.reference_fill_factor_pct - 1.0

    scaled = profile["control_power"] * (
        1.0
        + cfg.control_sa_sensitivity * strength_ratio
        + cfg.control_fill_factor_sensitivity * coverage_ratio
    )
    return float(max(0.0, min(scaled, 1.0)))


def design_features(recipe: DesignRecipe) -> FeatureVector:
    """Design IP features. Server-side only -- never logged to a client.

    Names carry the channel, so a model cannot learn from "SA" without knowing
    whether that is the back surface's aberration or the microstructure's
    modulation target. Conflating the two is the error the recipe split fixes.
    """
    nso = recipe.nso_modulation
    values = {
        # NSO microstructure channel
        "nso_peak_target_d": nso.peak_target_d,
        "nso_mean_fill_factor_pct": nso.mean_fill_factor_pct,
        "nso_temporal_modulation_pct": nso.temporal_modulation_pct,
        "nso_spatial_jitter_deg": nso.spatial_jitter_deg,
        "nso_entropy": nso.entropy,
        # Base surface channel
        "base_aspheric_sa_d": recipe.base_aspheric_sa_d,
        # Shared
        "target_mtf_modulation": recipe.target_mtf_modulation,
        "eye_axial_length": recipe.axial_length,
        "tier_control_power": engine.NSO_PROFILES[recipe.profile_tier][
            "control_power"
        ],
        "effective_control_power": _effective_control_power(recipe),
    }
    for zone in nso.zones:
        values[f"zone_{zone.zone}_target_d"] = nso.zone_targets_d[zone.zone]
        values[f"zone_{zone.zone}_height_um"] = zone.element_height_um
        values[f"zone_{zone.zone}_diameter_um"] = zone.element_diameter_um
        values[f"zone_{zone.zone}_fill_factor_pct"] = zone.fill_factor_pct
    return FeatureVector(kind="design", values=values)


def training_row(
    patient: FeatureVector,
    design: FeatureVector,
    outcome: Optional[Dict[str, Any]] = None,
    **meta: Any,
) -> Dict[str, Any]:
    """One flat row of training data.

    Log these as designs are dispensed, and attach ``outcome`` at follow-up
    (observed axial-length change, comfort report, whether the patient stayed
    in the lens). That table is the dataset; without it there is nothing to
    train on no matter how good the model architecture is.
    """
    row: Dict[str, Any] = {
        "schema_version": FeatureVector.SCHEMA_VERSION,
        "config_version": get_config().version,
    }
    row.update(meta)
    row.update(patient.prefixed())
    row.update(design.prefixed())
    if outcome:
        row.update({f"outcome__{k}": v for k, v in outcome.items()})
    return row
