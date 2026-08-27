"""
The V2.1 pipeline, stated as code.

The supervisor's V2.1 baseline replaces

    Clinical -> Candidate Geometry

with a chain whose stages are each replaceable on their own::

    Clinical Phenotype
      -> Functional Requirement Vector
      -> Optical Target Vector
      -> NSO Design Profile
      -> Geometry Projection
      -> Manufacturing Compensation
      -> Vendor-specific Execution Package

Every stage already existed somewhere in the package. What was missing was a
place where the chain is visible: the boundaries were implied by which module
called which, which makes them easy to erode and impossible to point at.

Naming the stages buys three things:

  * a platform change touches one stage. Moving to MR-8, PC or contact lenses
    replaces the design profile and everything downstream; the clinical stages
    do not know the product exists.
  * a vendor change touches one stage. The 3.0 -> 1.8 um coating loss lives in
    compensation, so it cannot leak backwards into the fitting rules.
  * each boundary is testable. ``run_pipeline`` returns every intermediate, so
    a test can assert that a clinical input reached stage 2 and stopped, or
    that a process change moved stage 6 and nothing else.

This module orchestrates; it holds no logic of its own. Each stage delegates to
the module that owns it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .design.joint import optimize_pair, pair_class
from .design.synthesis import ModulationTarget, modulation_target
from .features import ai_derived_indices
from .manufacturing.compensation import compensation_report
from .manufacturing.geometry import microstructure_map, surface_map
from .patient import PatientInput
from .phenotype import visual_phenotype
from .recipe import DesignRecipe

#: The stages, in order. Named here so tests and documentation cannot drift
#: from what the code actually runs.
STAGES = (
    "clinical_phenotype",
    "functional_requirement",
    "optical_target",
    "design_profile",
    "geometry_projection",
    "manufacturing_compensation",
    "execution_package",
)


@dataclass
class PipelineResult:
    """Every intermediate, so each boundary can be inspected and tested."""

    clinical_phenotype: Dict[str, Any]
    functional_requirement: Dict[str, ModulationTarget]
    optical_target: Dict[str, Dict[str, float]]
    design_profile: Dict[str, DesignRecipe]
    geometry_projection: Dict[str, Any] = field(default_factory=dict)
    manufacturing_compensation: Dict[str, Any] = field(default_factory=dict)
    execution_package: Dict[str, Any] = field(default_factory=dict)

    def stage(self, name: str) -> Any:
        if name not in STAGES:
            raise KeyError(f"unknown stage {name!r}; stages are {STAGES}")
        return getattr(self, name)


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #

def stage_clinical_phenotype(p: PatientInput) -> Dict[str, Any]:
    """1. Measurements -> indices and the R/B/S/N/T phenotype.

    Product-independent. Nothing here knows a lens exists.
    """
    indices = ai_derived_indices(p)
    return {"indices": indices, "phenotype": visual_phenotype(p, indices)}


def stage_functional_requirement(
    p: PatientInput, phenotype: Dict[str, Any]
) -> Dict[str, ModulationTarget]:
    """2. Phenotype -> normalized demand, per eye.

    Still product-independent: 0-1 variables describing what the eye needs, not
    what any particular lens should do about it. This is the boundary the
    supervisor asked for -- everything above it survives a platform change.
    """
    return {
        eye: modulation_target(p, eye, phenotype["indices"])
        for eye in ("OD", "OS")
    }


def stage_optical_target(
    design_profile: Dict[str, DesignRecipe]
) -> Dict[str, Dict[str, float]]:
    """3. Demand -> per-zone optical targets in dioptres.

    The first product-specific stage, and the last one expressed in optical
    rather than manufacturing terms. `3-5-4D` lives here.
    """
    return {
        eye: dict(recipe.nso_modulation.zone_targets_d)
        for eye, recipe in design_profile.items()
    }


def stage_design_profile(
    p: PatientInput, phenotype: Dict[str, Any]
) -> Dict[str, Any]:
    """4. Targets -> the chosen OD/OS design pair.

    Candidate generation and joint optimization. The pair is selected together,
    so a monocularly optimal design can lose to one that fuses better.
    """
    pairs, od_candidates, os_candidates = optimize_pair(p, phenotype["indices"])
    best = pairs[0]
    return {
        "pairs": pairs,
        "candidates": {"OD": od_candidates, "OS": os_candidates},
        "selected": {"OD": best["od"], "OS": best["os"]},
        "recipes": {"OD": best["od"]["recipe"], "OS": best["os"]["recipe"]},
        "pair_class": pair_class(best["od"]["recipe"], best["os"]["recipe"]),
        "binocular_penalty": best["binocular_penalty"],
    }


def stage_geometry_projection(
    recipes: Dict[str, DesignRecipe], page: int = 0
) -> Dict[str, Any]:
    """5. Design -> coordinates, one path per optical channel.

    Back surface becomes a sag map, front microstructure a placement list. The
    two never mix: that separation is the point of the channel split.
    """
    return {
        eye: {
            "base_surface": surface_map(recipe),
            "nso_modulation": microstructure_map(recipe, page=page),
        }
        for eye, recipe in recipes.items()
    }


def stage_manufacturing_compensation(
    recipes: Dict[str, DesignRecipe]
) -> Dict[str, Any]:
    """6. Design geometry -> what the machine should cut.

    A coating line is not a design decision, so its losses live here and cannot
    reach back into the fitting rules.
    """
    return {eye: compensation_report(recipe) for eye, recipe in recipes.items()}


def stage_execution_package(compensation: Dict[str, Any]) -> Dict[str, Any]:
    """7. Compensated geometry -> what each vendor is allowed to see.

    The segmented release itself is issued by the registry against a Design ID
    and a vendor key; this stage reports what would be shipped, without
    granting anyone access to it.
    """
    return {
        "segments": ["FS", "BS", "AV"],
        "formable": all(c["all_formable"] for c in compensation.values()),
        "note": (
            "Packages are released per vendor through the manufacturing API. "
            "No single vendor receives more than one segment."
        ),
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_pipeline(
    p: PatientInput, *, through: Optional[str] = None, page: int = 0
) -> PipelineResult:
    """Run the chain, optionally stopping early.

    ``through`` stops after the named stage, which is what makes the boundaries
    testable: a caller can ask for the functional requirement without paying
    for geometry, and a test can assert that a clinical input moved stage 2 and
    left stage 5 alone.
    """
    if through is not None and through not in STAGES:
        raise KeyError(f"unknown stage {through!r}; stages are {STAGES}")

    def reached(stage: str) -> bool:
        return through is None or STAGES.index(stage) <= STAGES.index(through)

    phenotype = stage_clinical_phenotype(p)
    requirement = stage_functional_requirement(p, phenotype)

    result = PipelineResult(
        clinical_phenotype=phenotype,
        functional_requirement=requirement,
        optical_target={},
        design_profile={},
    )
    if not reached("optical_target"):
        return result

    profile = stage_design_profile(p, phenotype)
    result.design_profile = profile
    result.optical_target = stage_optical_target(profile["recipes"])
    if not reached("geometry_projection"):
        return result

    result.geometry_projection = stage_geometry_projection(
        profile["recipes"], page=page)
    if not reached("manufacturing_compensation"):
        return result

    result.manufacturing_compensation = stage_manufacturing_compensation(
        profile["recipes"])
    if not reached("execution_package"):
        return result

    result.execution_package = stage_execution_package(
        result.manufacturing_compensation)
    return result


def describe_pipeline() -> List[Dict[str, str]]:
    """The chain and what each stage owns. Used by docs and by /api/health."""
    return [
        {"stage": "clinical_phenotype",
         "owns": "measurements -> indices and R/B/S/N/T",
         "product_specific": "no"},
        {"stage": "functional_requirement",
         "owns": "phenotype -> normalized 0-1 demand vector",
         "product_specific": "no"},
        {"stage": "optical_target",
         "owns": "demand -> per-zone add targets in dioptres",
         "product_specific": "yes"},
        {"stage": "design_profile",
         "owns": "candidate generation and joint OD/OS optimization",
         "product_specific": "yes"},
        {"stage": "geometry_projection",
         "owns": "sag map and microstructure placement",
         "product_specific": "yes"},
        {"stage": "manufacturing_compensation",
         "owns": "process transfer -- design height to cut height",
         "product_specific": "vendor"},
        {"stage": "execution_package",
         "owns": "segmented, per-vendor release",
         "product_specific": "vendor"},
    ]
