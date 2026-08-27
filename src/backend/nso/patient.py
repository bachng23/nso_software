"""
Patient input — the six sections of the V2 clinical profile.

Everything outside Quick Fitting (Tier 1) is optional: the engine degrades
gracefully and reports which domains were measured rather than requiring every
possible parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import get_config
from .csf import CsfMeasurement, from_triplet

PRIMARY_GOALS = (
    "Myopia Management",
    "Digital Visual Comfort",
    "Reading",
    "Near Work",
    "Presbyopia",
    "Driving",
    "Night Vision",
    "Sports Vision",
    "General Visual Comfort",
)


@dataclass
class EyeInput:
    """Per-eye refractive data (Section 1)."""
    sphere: float = 0.0
    cylinder: float = 0.0
    axis: float = 0.0
    axial_length: float = 24.0
    bcva_logmar: Optional[float] = None

    @property
    def spherical_equivalent(self) -> float:
        return self.sphere + self.cylinder / 2.0


@dataclass
class PatientInput:
    """The full six-section V2 profile. Tier 1 (Quick Fitting) fields first."""

    # -- Section 1: patient & refractive -----------------------------------
    age: float = 12.0
    od: EyeInput = field(default_factory=EyeInput)
    os: EyeInput = field(default_factory=EyeInput)
    photopic_pupil: float = 4.5
    mesopic_pupil: Optional[float] = None

    # -- Section 2: binocular vision ---------------------------------------
    near_phoria: float = 0.0          # prism dioptres, exo negative
    npc: float = 7.0                  # cm
    distance_phoria: Optional[float] = None
    pfv: Optional[float] = None
    nfv: Optional[float] = None
    ac_a: Optional[float] = None
    stereoacuity: Optional[float] = None   # arc sec
    ocular_dominance: str = "Balanced"     # OD / OS / Balanced
    binocular_balance: str = "Normal"      # Normal / Mild / Significant

    # -- Section 3: accommodation ------------------------------------------
    accommodative_lag: float = 0.75   # D
    amplitude_of_accommodation: Optional[float] = None
    accommodative_facility: Optional[float] = None
    near_working_distance: Optional[float] = None
    computer_working_distance: Optional[float] = None

    # -- Section 4: spatial frequency --------------------------------------
    #
    # Three ways to supply this, most informative first:
    #
    #   csf_measurement   a full curve with its own device and frequencies
    #   csf_low/mid/high  three values, frequencies assumed unless given
    #   csf_band          Low / Mid / High, a label rather than a measurement
    #
    # The band is a fallback, not a measurement -- see ``csf_is_measured``.
    csf_band: str = "Mid"
    csf_low: Optional[float] = None
    csf_mid: Optional[float] = None
    csf_high: Optional[float] = None
    csf_measurement: Optional[CsfMeasurement] = None
    #: Frequencies the triplet was taken at, when the clinic reports them.
    csf_frequencies_cpd: Optional[List[float]] = None
    csf_device: str = "unspecified"
    csf_test_protocol: str = "unspecified"
    csf_scale: str = "index_0_100"

    # -- Section 5: neurovisual --------------------------------------------
    visual_stress_score: float = 3.0  # 0-10, patient-reported
    visual_comfort_score: Optional[float] = None       # 0-10
    neural_adaptation_score: Optional[float] = None    # 0-10
    dynamic_visual_stability: Optional[float] = None   # 0-10
    # A bare number is not a measurement: without a stimulus, a unit and a
    # normative reference, nobody can say whether it is good or bad. The V2.1
    # baseline asks for the structured form, and for confidence NOT to rise
    # merely because a test was performed.
    vep: Optional[float] = None                    # legacy bare value
    vep_stimulus: str = "unspecified"              # e.g. "pattern-reversal 1 deg"
    vep_amplitude_uv: Optional[float] = None
    vep_latency_ms: Optional[float] = None
    vep_interocular_difference_ms: Optional[float] = None
    vep_z_score: Optional[float] = None            # against the lab's normative data

    erg: Optional[float] = None                    # legacy bare value
    erg_protocol: str = "unspecified"
    erg_z_score: Optional[float] = None

    # Eye tracking is the one the supervisor expects to enter the optimizer
    # first, so its sub-metrics are recorded individually rather than as one
    # opaque score.
    eye_tracking: Optional[float] = None           # legacy bare value
    fixation_stability: Optional[float] = None     # arcmin BCEA or equivalent
    blink_rate: Optional[float] = None             # blinks/min
    vergence_stability: Optional[float] = None     # 0-10
    pupil_dynamics: Optional[float] = None         # 0-10
    gaze_distribution: Optional[float] = None      # 0-10

    # -- Section 6: task & lifestyle ---------------------------------------
    primary_goal: str = "Myopia Management"
    near_hours: float = 6.0
    digital_hours: float = 4.0
    outdoor_hours: float = 1.5
    typical_working_distance: Optional[float] = None
    night_driving: bool = False
    low_light_demand: str = "Moderate"   # Low / Moderate / High

    # -- Closed-loop state (set by ``refit``, never by a client) -----------
    progression_load: float = 0.0

    # -- Research tier (Section 1 advanced) --------------------------------
    hoa_rms: Optional[float] = None
    corneal_sa: Optional[float] = None
    coma: Optional[float] = None
    trefoil: Optional[float] = None
    corneal_astigmatism: Optional[float] = None   # D
    corneal_eccentricity: Optional[float] = None  # shape factor e

    # ---------------------------------------------------------------- utils
    def csf(self) -> Optional[CsfMeasurement]:
        """The measurement, if one was taken.

        A full curve wins over a triplet; a band is not a measurement and
        returns None here.
        """
        if self.csf_measurement is not None:
            return self.csf_measurement
        triplet = (self.csf_low, self.csf_mid, self.csf_high)
        if all(v is not None for v in triplet):
            return from_triplet(
                *triplet,
                frequencies=self.csf_frequencies_cpd,
                device=self.csf_device,
                test_protocol=self.csf_test_protocol,
                scale=self.csf_scale,
            )
        return None

    @property
    def has_interpretable_neurovisual(self) -> bool:
        """True when a neurovisual result can actually be read.

        A z-score is interpretable on its own. Eye-tracking sub-metrics are
        interpretable because each has a defined scale. A bare ``vep=1.5`` is
        not: it is a record, not evidence.
        """
        return any(
            v is not None
            for v in (
                self.vep_z_score,
                self.erg_z_score,
                self.fixation_stability,
                self.blink_rate,
                self.vergence_stability,
                self.pupil_dynamics,
                self.gaze_distribution,
            )
        )

    @property
    def neurovisual_recorded(self) -> bool:
        """True when anything neurovisual was entered, interpretable or not.

        Recorded values are kept for the future dataset even when they cannot
        be used today -- ``record -> normalize -> confidence`` in that order.
        """
        return any(
            v is not None for v in (self.vep, self.erg, self.eye_tracking)
        ) or self.has_interpretable_neurovisual

    @property
    def csf_is_measured(self) -> bool:
        return self.csf() is not None

    def csf_value(self) -> float:
        """0-100 CSF quality.

        From the measured curve when there is one. Otherwise from the band,
        which is a clinician's impression rather than a measurement -- the
        mapping is configurable precisely because it should not harden into a
        device-specific constant.
        """
        measurement = self.csf()
        if measurement is not None:
            quality = measurement.quality_0_100()
            if quality is not None:
                return quality
        bands = get_config().csf_bands
        return bands.get(self.csf_band, bands["Mid"])

    def comfort_value(self) -> float:
        """0-100 comfort tolerance, from the 0-10 comfort or stress score."""
        if self.visual_comfort_score is not None:
            return float(self.visual_comfort_score) * 10.0
        return float(max(0.0, 10.0 - self.visual_stress_score)) * 10.0

    def measured_domains(self) -> Dict[str, bool]:
        """Which optional domains actually carry data (drives confidence)."""
        return {
            "binocular_extended": any(
                v is not None for v in (self.pfv, self.nfv, self.ac_a, self.stereoacuity)
            ),
            "accommodation_extended": any(
                v is not None
                for v in (self.amplitude_of_accommodation, self.accommodative_facility)
            ),
            "csf_measured": any(
                v is not None for v in (self.csf_low, self.csf_mid, self.csf_high)
            ),
            # Only an INTERPRETABLE neurovisual result counts. A raw VEP number
            # with no stimulus, unit or normative reference cannot inform the
            # fitting, so crediting it would inflate confidence for having run
            # a test rather than for having learned anything -- exactly what
            # the V2.1 baseline says not to do.
            "neurovisual_extended": self.has_interpretable_neurovisual,
            "wavefront": any(
                v is not None
                for v in (self.hoa_rms, self.corneal_sa, self.coma, self.trefoil)
            ),
        }
