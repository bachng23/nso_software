"""
Phenotype -> optical design, in two separate channels.

The supervisor's V2.1 baseline splits what used to be one step:

    patient  ->  D / H / FF                    (what this used to do -- wrong)

    patient  ->  optical modulation target
             ->  NSO profile
             ->  zone geometry                 (what it does now)

The first form collapsed "clinical phenotype -> optical target" and "optical
target -> manufacturable geometry" into a single set of formulas. Separating
them is not tidiness: it means the clinical layer keeps working when the
platform moves to MR-8, PC, contact lenses, or a different manufacturing
vendor, because only the second stage is product-specific.

The clinical stage therefore emits normalized 0-1 demand variables, and the
design stage turns those into zone geometry using the supervisor's reference
table. ASSUMPTIONS.md P1-21 records which parts of the second stage are
measured and which are still interpolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import nso_core as engine

from ..config import get_config
from ..features import (
    corneal_asphericity_departure,
    corneal_sa_departure,
    dominance_bias,
    eye_risk,
    residual_aberration_load,
)
from ..patient import PatientInput
from ..recipe import (
    ZONE_LABELS,
    BaseSurfaceProfile,
    DesignRecipe,
    NsoModulationProfile,
    ZoneGeometry,
)


# --------------------------------------------------------------------------- #
# Stage 1 — clinical phenotype to a functional requirement vector
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ModulationTarget:
    """What the eye needs, on a normalized 0-1 scale.

    Product-independent on purpose. This is the vector the supervisor asked the
    fitting engine to produce instead of geometry, so that a change of lens
    platform or vendor does not reach the clinical layer.
    """

    control_demand: float          # how much myopia-control signal is needed
    visual_tolerance: float        # how much optical disturbance is acceptable
    contrast_reserve: float        # headroom in contrast sensitivity
    peripheral_modulation: float   # how strong the peripheral signal should be
    temporal_asymmetry: float      # temporal/nasal weighting

    def as_dict(self) -> Dict[str, float]:
        return {
            "control_demand": round(self.control_demand, 4),
            "visual_tolerance": round(self.visual_tolerance, 4),
            "contrast_reserve": round(self.contrast_reserve, 4),
            "peripheral_modulation": round(self.peripheral_modulation, 4),
            "temporal_asymmetry": round(self.temporal_asymmetry, 4),
        }


def _clamp01(x: float) -> float:
    return float(max(0.0, min(x, 1.0)))


def modulation_target(
    p: PatientInput, eye_key: str, indices: Dict[str, float]
) -> ModulationTarget:
    """Clinical phenotype -> normalized functional requirement.

    ASSUMPTIONS P1-5: the weights here are still uncalibrated. What changed is
    that they now produce a product-independent demand vector rather than
    micrometres, so recalibrating them cannot invalidate the geometry stage.
    """
    cfg = get_config()
    eye = p.od if eye_key == "OD" else p.os

    stress = indices["visual_stress"] / 100.0
    bino = indices["binocular_load"] / 100.0
    accom = indices["accommodative_stress"] / 100.0
    csf_q = indices["spatial_frequency_sensitivity"] / 100.0

    # Per-eye asymmetry: the more loaded eye asks for more signal.
    mean_risk = (eye_risk(p.od) + eye_risk(p.os)) / 2.0
    asymmetry = eye_risk(eye) - mean_risk
    asymmetry -= cfg.dominance_asymmetry_gain * dominance_bias(p, eye_key)

    control = _clamp01(
        0.55 * eye_risk(eye)
        + 0.20 * accom
        + 0.25 * p.progression_load
        + 0.90 * asymmetry
    )
    # What the eye already carries reduces what the lens should add. Both gains
    # are bounded modifiers, not dioptric subtractions -- see P1-19.
    control = _clamp01(
        control
        * (1.0 - cfg.sa_corneal_compensation * _clamp01(corneal_sa_departure(p) / 0.4))
        * (1.0 - cfg.sa_corneal_asphericity_gain
                 * _clamp01(corneal_asphericity_departure(p) / 0.4))
    )

    tolerance = _clamp01(1.0 - stress)
    tolerance = _clamp01(
        tolerance * (1.0 - cfg.sa_hoa_tolerance_gain * residual_aberration_load(p))
    )

    return ModulationTarget(
        control_demand=control,
        visual_tolerance=tolerance,
        contrast_reserve=_clamp01(csf_q),
        peripheral_modulation=_clamp01(0.65 * control + 0.35 * tolerance),
        temporal_asymmetry=_clamp01(0.5 + 0.5 * bino),
    )


# --------------------------------------------------------------------------- #
# Stage 2 — modulation target to zone geometry
# --------------------------------------------------------------------------- #

def _scaled_zone(
    label: str,
    target_d: float,
    reference_target_d: float,
    target: ModulationTarget,
    coverage_bias: float = 0.0,
) -> ZoneGeometry:
    """One zone's geometry, scaled from the supervisor's reference row.

    Two things move the geometry, and both must:

      * the zone's OPTICAL TARGET, which is a property of the profile tier
      * the patient's DEMAND VECTOR, which is what the clinical layer produces

    The tier target alone is quantized -- three tiers, three sets of numbers --
    so if only that reached the geometry, every patient in a tier would receive
    an identical lens and the whole clinical layer would be decorative. The
    demand vector is what makes two patients in the same tier differ.

    The reference geometry is measured. How it scales away from the reference
    tier is NOT -- that is the interpolation recorded in ASSUMPTIONS P1-21.
    Height takes most of the scaling because it carries the modulation depth;
    diameter moves least.
    """
    cfg = get_config()
    ref = cfg.zone_reference_geometry[label]
    ratio = target_d / reference_target_d if reference_target_d else 1.0

    # How much of the available target this eye actually asks for. A high
    # control demand realizes the target; low tolerance pulls it back.
    realized = 0.80 + 0.40 * target.control_demand
    trim = 0.85 + 0.15 * target.visual_tolerance

    # Outer zones carry the peripheral signal, so peripheral demand weights
    # them more than the central zone.
    peripheral_weight = {"A": 0.0, "B": 0.5, "C": 1.0}[label]
    realized *= 1.0 + 0.20 * peripheral_weight * (target.peripheral_modulation - 0.5)

    # Preserving contrast means putting less area under structure.
    contrast_relief = 1.0 - 0.25 * (1.0 - target.contrast_reserve)

    height = (
        ref["height_um"]
        * (ratio ** cfg.zone_height_target_exponent)
        * realized * trim
    )
    # coverage_bias is the candidate grid's second axis. It has to reach the
    # fill factor directly: routing it through the tolerance term only moved
    # height, which left coverage identical across the whole grid and made the
    # axis inert -- the same failure the single-axis grid had.
    fill = (
        ref["fill_factor_pct"]
        * (ratio ** cfg.zone_fill_target_exponent)
        * realized * contrast_relief
    ) + coverage_bias
    diameter = ref["diameter_um"] * (ratio ** cfg.zone_diameter_target_exponent)
    length = ref["length_um"] * (ratio ** cfg.zone_diameter_target_exponent)

    bounds = cfg.zone_boundaries_mm
    i = ZONE_LABELS.index(label)

    return ZoneGeometry(
        zone=label,
        element_diameter_um=round(diameter, 2),
        element_length_um=round(length, 2),
        element_height_um=round(
            max(cfg.zone_height_min_um, min(height, cfg.zone_height_max_um)), 3),
        fill_factor_pct=round(
            max(cfg.zone_fill_min_pct, min(fill, cfg.zone_fill_max_pct)), 2),
        inner_radius_mm=bounds[i],
        outer_radius_mm=bounds[i + 1],
    )


def nso_modulation_profile(
    eye_key: str,
    tier: str,
    target: ModulationTarget,
    target_bias_d: float = 0.0,
    coverage_bias: float = 0.0,
) -> NsoModulationProfile:
    """Build the anterior microstructure channel."""
    cfg = get_config()
    reference = cfg.zone_optical_targets_d[cfg.zone_reference_tier]
    targets = {
        label: max(0.5, value + target_bias_d)
        for label, value in cfg.zone_optical_targets_d[tier].items()
    }

    zones = [
        _scaled_zone(label, targets[label], reference[label], target, coverage_bias)
        for label in ZONE_LABELS
    ]

    jitter = round(
        cfg.jitter_base_deg
        + cfg.jitter_csf_gain_deg * (1.0 - target.contrast_reserve)
        + cfg.jitter_stress_gain_deg * (1.0 - target.visual_tolerance),
        1,
    )
    temporal = round(cfg.temporal_modulation_pct * (0.5 + target.temporal_asymmetry), 1)

    mean_fill = sum(z.fill_factor_pct for z in zones) / len(zones)
    entropy = engine.entropy_score(
        density=mean_fill * 2.0,
        temporal_multiplier=1.0 + temporal / 100.0,
        sa_strength=max(targets.values()),
    )

    return NsoModulationProfile(
        eye=eye_key,
        profile_tier=tier,
        label="-".join(f"{targets[l]:g}" for l in ZONE_LABELS) + "D",
        zone_targets_d={k: round(v, 2) for k, v in targets.items()},
        zones=zones,
        temporal_modulation_pct=temporal,
        spatial_jitter_deg=jitter,
        entropy=round(entropy, 1),
    )


def base_surface_profile(p: PatientInput, eye_key: str) -> BaseSurfaceProfile:
    """Build the posterior surface channel.

    Prescription plus any low-order aspheric term. The aspheric term is a
    property of the SURFACE and is never derived from the microstructure
    modulation -- that conflation is the error this split exists to fix. It is
    zero until a value is specified.
    """
    cfg = get_config()
    eye = p.od if eye_key == "OD" else p.os
    return BaseSurfaceProfile(
        eye=eye_key,
        sphere=eye.sphere,
        cylinder=eye.cylinder,
        axis=eye.axis,
        aspheric_sa_d=cfg.base_surface_aspheric_sa_d,
        functional_zone_semi_diameter_mm=cfg.sa_reference_semi_diameter_mm,
        rolloff_blend_mm=cfg.sa_rolloff_blend_mm,
    )


def design_for_eye(
    p: PatientInput,
    eye_key: str,
    indices: Dict[str, float],
    sa_bias: float = 0.0,
    density_bias: float = 0.0,
) -> DesignRecipe:
    """Full two-channel design for one eye.

    ``sa_bias`` and ``density_bias`` are the candidate-search offsets. They
    shift the OPTICAL TARGET and the tolerance trim respectively; neither
    touches the base surface.
    """
    cfg = get_config()
    eye = p.od if eye_key == "OD" else p.os

    pred = engine.run_prediction(
        age=p.age,
        al=eye.axial_length,
        myopia=eye.spherical_equivalent,
        pupil=p.photopic_pupil,
        near_hours=p.near_hours,
        outdoor_hours=p.outdoor_hours,
        csf_score=p.csf_value(),
        comfort_score=p.comfort_value(),
    )
    tier = pred["profile"]

    target = modulation_target(p, eye_key, indices)
    nso = nso_modulation_profile(
        eye_key, tier, target,
        target_bias_d=sa_bias, coverage_bias=density_bias,
    )
    base = base_surface_profile(p, eye_key)

    profile = engine.NSO_PROFILES[tier]
    target_mtf = round(
        max(
            cfg.target_mtf_floor,
            cfg.target_mtf_ceiling
            - profile["mtf_reduction"]
            + cfg.target_mtf_csf_gain * (1.0 - target.contrast_reserve)
            + cfg.target_mtf_sa_gain * (nso.peak_target_d - profile["sa_strength"]),
        ),
        3,
    )

    return DesignRecipe(
        eye=eye_key,
        axial_length=eye.axial_length,
        base_surface=base,
        nso_modulation=nso,
        profile_tier=tier,
        target_mtf_modulation=target_mtf,
        entropy=nso.entropy,
    )
