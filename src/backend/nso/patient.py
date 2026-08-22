"""
Patient input — the six sections of the V2 clinical profile.

Everything outside Quick Fitting (Tier 1) is optional: the engine degrades
gracefully and reports which domains were measured rather than requiring every
possible parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from .config import get_config

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
    csf_band: str = "Mid"             # Quick Fitting: Low / Mid / High
    csf_low: Optional[float] = None
    csf_mid: Optional[float] = None
    csf_high: Optional[float] = None

    # -- Section 5: neurovisual --------------------------------------------
    visual_stress_score: float = 3.0  # 0-10, patient-reported
    visual_comfort_score: Optional[float] = None       # 0-10
    neural_adaptation_score: Optional[float] = None    # 0-10
    dynamic_visual_stability: Optional[float] = None   # 0-10
    vep: Optional[float] = None
    erg: Optional[float] = None
    eye_tracking: Optional[float] = None

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
    def csf_value(self) -> float:
        """Resolve a 0-100 CSF quality from the band or the measured triplet."""
        measured = [v for v in (self.csf_low, self.csf_mid, self.csf_high) if v is not None]
        if measured:
            return float(sum(measured) / len(measured))
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
            "neurovisual_extended": any(
                v is not None for v in (self.vep, self.erg, self.eye_tracking)
            ),
            "wavefront": any(
                v is not None
                for v in (self.hoa_rms, self.corneal_sa, self.coma, self.trefoil)
            ),
        }
