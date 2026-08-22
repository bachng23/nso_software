"""
The predictor interface.

A predictor answers one question: given this patient and this candidate design,
what outcomes are expected? Today that is answered by rules; later it will be
answered by a trained model. Both sit behind :class:`Predictor`, so swapping
them changes one registry entry and nothing else.

What the interface deliberately does NOT let a predictor do:

  * choose a design — that stays in ``nso.design``, so a model can be replaced
    or rolled back without changing which designs are reachable;
  * see raw patient objects — only :class:`FeatureVector`, so training and
    serving cannot drift apart;
  * ship an unlabelled answer — every :class:`OutcomePrediction` carries the
    predictor name, its version and the config version, so any stored result
    can be traced to what produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, runtime_checkable

from ..features import FeatureVector


@dataclass(frozen=True)
class OutcomePrediction:
    """Predicted outcomes for one (patient, design) pair. All 0-100."""

    control: float
    comfort: float
    adaptation: float
    acuity: float
    manufacturability: float

    predictor_name: str
    predictor_version: str
    schema_version: str
    config_version: str

    # Populated by models that produce them; rule-based leaves it empty.
    uncertainty: Dict[str, float] = field(default_factory=dict)

    def as_metrics(self) -> Dict[str, float]:
        """The numeric outcomes only, rounded for display and comparison."""
        return {
            "control": round(self.control, 1),
            "comfort": round(self.comfort, 1),
            "adaptation": round(self.adaptation, 1),
            "acuity": round(self.acuity, 1),
            "manufacturability": round(self.manufacturability, 1),
        }

    def provenance(self) -> Dict[str, str]:
        return {
            "predictor": self.predictor_name,
            "predictor_version": self.predictor_version,
            "feature_schema": self.schema_version,
            "config_version": self.config_version,
        }


@runtime_checkable
class Predictor(Protocol):
    """What every predictor — rule-based or learned — must provide."""

    name: str
    version: str
    #: Feature schema this predictor was built against. Checked at call time.
    schema_version: str

    def predict(
        self, patient: FeatureVector, design: FeatureVector
    ) -> OutcomePrediction:
        ...


class SchemaMismatchError(RuntimeError):
    """A predictor was given features from a schema it was not built for.

    Raised loudly on purpose: a model silently reading a reordered or renamed
    feature vector produces confident nonsense, which is far worse than an
    error.
    """


def check_schema(predictor: Predictor, *vectors: FeatureVector) -> None:
    for v in vectors:
        if v.SCHEMA_VERSION != predictor.schema_version:
            raise SchemaMismatchError(
                f"{predictor.name} {predictor.version} expects feature schema "
                f"{predictor.schema_version}, got {v.SCHEMA_VERSION}"
            )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_REGISTRY: Dict[str, Predictor] = {}
_ACTIVE: str = "rule_based"


def register(predictor: Predictor, *, activate: bool = False) -> None:
    """Make a predictor available by name. ``activate`` also selects it."""
    _REGISTRY[predictor.name] = predictor
    if activate:
        use_predictor(predictor.name)


def use_predictor(name: str) -> str:
    """Select the active predictor. Returns the previous one."""
    global _ACTIVE
    if name not in _REGISTRY:
        raise KeyError(f"unknown predictor '{name}'; registered: {sorted(_REGISTRY)}")
    previous, _ACTIVE = _ACTIVE, name
    return previous


def get_predictor(name: str | None = None) -> Predictor:
    return _REGISTRY[name or _ACTIVE]


def available() -> Dict[str, Dict[str, Any]]:
    return {
        n: {"version": p.version, "schema": p.schema_version, "active": n == _ACTIVE}
        for n, p in _REGISTRY.items()
    }
