"""Predictors: the swap point between rules and a trained model."""

from .base import (
    OutcomePrediction,
    Predictor,
    SchemaMismatchError,
    available,
    check_schema,
    get_predictor,
    register,
    use_predictor,
)
from .rule_based import RuleBasedPredictor

register(RuleBasedPredictor(), activate=True)

__all__ = [
    "OutcomePrediction", "Predictor", "RuleBasedPredictor",
    "SchemaMismatchError", "available", "check_schema", "get_predictor",
    "register", "use_predictor",
]
