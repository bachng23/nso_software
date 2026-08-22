"""
Phenotype -> optical design.

ASSUMPTIONS.md P1-5: this is the commercially valuable mapping and the least
evidenced part of the system. Every coefficient lives in ``EngineConfig``, so
calibrating it is a config change; the structure of the mapping is here.
"""

from __future__ import annotations

from typing import Dict

import nso_core as engine
from nso_core import norm

from ..config import get_config
from ..features import (
    corneal_asphericity_departure,
    corneal_sa_departure,
    dominance_bias,
    eye_risk,
    residual_aberration_load,
    vergence_direction,
)
from ..patient import PatientInput
from ..recipe import DesignRecipe


def design_for_eye(
    p: PatientInput,
    eye_key: str,
    indices: Dict[str, float],
    sa_bias: float = 0.0,
    density_bias: float = 0.0,
) -> DesignRecipe:
    cfg = get_config()
    eye = p.od if eye_key == "OD" else p.os

    # Asymmetry term: how much this eye departs from the binocular mean. It is
    # zero for a symmetric patient, so isometropic cases stay symmetric.
    mean_risk = (eye_risk(p.od) + eye_risk(p.os)) / 2.0
    asymmetry = eye_risk(eye) - mean_risk
    # The dominant eye carries more of the visual task and may warrant the
    # gentler design. Gain zero pending clinical sign-off.
    asymmetry -= cfg.dominance_asymmetry_gain * dominance_bias(p, eye_key)

    pred = engine.run_prediction(
        age=p.age,
        al=eye.axial_length,
        myopia=eye.spherical_equivalent,
        pupil=p.photopic_pupil,
        near_hours=p.near_hours + 0.5 * p.digital_hours,
        outdoor_hours=p.outdoor_hours,
        csf_score=p.csf_value(),
        comfort_score=p.comfort_value(),
    )
    tier = pred["profile"]
    profile = engine.NSO_PROFILES[tier]

    stress = indices["visual_stress"] / 100.0
    bino = indices["binocular_load"] / 100.0
    accom = indices["accommodative_stress"] / 100.0
    csf_q = indices["spatial_frequency_sensitivity"] / 100.0

    # An esophore at near is relieved by a peripheral add, an exophore burdened
    # by it, so the sign of the phoria -- not just its size -- belongs here. The
    # gain is zero until a clinician sets it; the path is wired and tested so
    # that setting it is the only change needed (ASSUMPTIONS P1-18).
    vergence = vergence_direction(p)

    # What the eye already brings. Subtracting the patient's own spherical
    # aberration and peripheral profile from what the lens adds is the whole
    # point of measuring them; capping the load when the retinal image is
    # already degraded is the other half. All three gains ship at zero -- adding
    # the wrong amount of SA is worse than adding none (ASSUMPTIONS P1-19).
    own_sa = corneal_sa_departure(p)
    own_asphericity = corneal_asphericity_departure(p)
    aberration_load = residual_aberration_load(p)

    sa = profile["sa_strength"] * (
        1.0
        + cfg.sa_accommodative_gain * accom
        + cfg.sa_stress_gain * stress
        + cfg.sa_asymmetry_gain * asymmetry
        + cfg.sa_progression_gain * p.progression_load
        + cfg.sa_vergence_direction_gain * vergence
        - cfg.sa_corneal_compensation * own_sa
        - cfg.sa_corneal_asphericity_gain * own_asphericity
        - cfg.sa_hoa_tolerance_gain * aberration_load
    ) + sa_bias
    sa = round(max(cfg.sa_min_d, min(sa, cfg.sa_max_d)), 2)

    density = profile["density_numeric"] * (
        1.0
        + cfg.density_stress_gain * stress
        + cfg.density_binocular_gain * bino
        + cfg.density_asymmetry_gain * asymmetry
    ) + density_bias
    density = max(cfg.density_min, min(density, cfg.density_max))

    # Microstructure geometry: larger elements for larger pupils, lower fill
    # factor when contrast sensitivity must be preserved.
    diameter = round(
        cfg.element_diameter_base_um
        + cfg.element_diameter_pupil_gain_um * norm(p.photopic_pupil, 3.0, 6.5)
        + cfg.element_diameter_density_gain_um * density,
        1,
    )
    height = round(
        cfg.element_height_sa_gain_um * sa
        + cfg.element_height_csf_gain_um * (1.0 - csf_q)
        + cfg.element_height_base_um,
        2,
    )
    fill_factor = round(
        cfg.fill_factor_base_pct
        + cfg.fill_factor_density_gain * density
        + cfg.fill_factor_csf_gain * (1.0 - csf_q),
        1,
    )
    density_mm2 = round(fill_factor / (3.1416 * (diameter / 2000.0) ** 2) / 100.0, 1)

    # Blue-noise jitter: more jitter when high-frequency CSF must be protected.
    jitter = round(
        cfg.jitter_base_deg
        + cfg.jitter_csf_gain_deg * (1.0 - csf_q)
        + cfg.jitter_stress_gain_deg * stress,
        1,
    )
    temporal = round(profile["temporal"] * (1.0 + cfg.temporal_binocular_gain * bino), 3)
    target_mtf = round(
        max(
            cfg.target_mtf_floor,
            cfg.target_mtf_ceiling
            - profile["mtf_reduction"]
            + cfg.target_mtf_csf_gain * (1.0 - csf_q)
            # Optical load costs acuity, symmetrically: a design carrying more
            # SA than its tier's nominal value gives some up, one carrying less
            # gets some back. The tier's own mtf_reduction covers the nominal.
            + cfg.target_mtf_sa_gain * (sa - profile["sa_strength"]),
        ),
        3,
    )
    entropy = engine.entropy_score(
        density=density, temporal_multiplier=temporal, sa_strength=sa
    )

    return DesignRecipe(
        eye=eye_key,
        axial_length=eye.axial_length,
        base_sphere=eye.sphere,
        base_cylinder=eye.cylinder,
        base_axis=eye.axis,
        sa_profile=profile["SA"],
        sa_strength=sa,
        microstructure_diameter_um=diameter,
        microstructure_height_um=height,
        fill_factor_pct=fill_factor,
        spatial_density_per_mm2=density_mm2,
        spatial_jitter_deg=jitter,
        temporal_nasal_ratio=temporal,
        target_mtf_modulation=target_mtf,
        zone_count=cfg.zone_count,
        entropy=round(entropy, 1),
        profile_tier=tier,
    )
