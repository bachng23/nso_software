"""
The deterministic predictor in use today.

It reproduces the v0.3/V2 rules exactly, but behind the ``Predictor``
interface, reading only feature vectors. That restriction is the point: it
proves the interface is sufficient for a real predictor before any model
depends on it.
"""

from __future__ import annotations

import nso_core as engine

from ..config import get_config
from ..features import FeatureVector, clip100
from .base import OutcomePrediction, check_schema


class RuleBasedPredictor:
    """Predicts outcomes from fixed formulas. No training, no data."""

    name = "rule_based"
    version = "2.0.0"
    schema_version = FeatureVector.SCHEMA_VERSION

    def predict(
        self, patient: FeatureVector, design: FeatureVector
    ) -> OutcomePrediction:
        check_schema(self, patient, design)
        cfg = get_config()
        pv, dv = patient.values, design.values

        control = engine.control_score(
            pv["age"],
            dv["eye_axial_length"],
            pv["near_hours"],
            pv["outdoor_hours"],
            dv["effective_control_power"] * 100,
        )
        comfort = clip100(
            100
            - engine.visual_stress(
                dv["entropy"], dv["sa_strength"], pv["photopic_pupil"],
                pv["comfort_value"],
            )
        )
        adaptation = engine.neural_adaptation(
            pv["age"],
            pv["comfort_value"],
            pv["csf_value"],
            dv["entropy"],
        )
        acuity = dv["target_mtf_modulation"] * 100
        manufacturability = clip100(
            100
            - 0.35 * dv["fill_factor_pct"]
            - 4.0 * max(0.0, dv["microstructure_height_um"] - 2.5)
        )

        return OutcomePrediction(
            control=control,
            comfort=comfort,
            adaptation=adaptation,
            acuity=acuity,
            manufacturability=manufacturability,
            predictor_name=self.name,
            predictor_version=self.version,
            schema_version=patient.SCHEMA_VERSION,
            config_version=cfg.version,
        )
