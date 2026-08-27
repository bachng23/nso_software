"""
The design recipe — Design IP layer.

TWO INDEPENDENT OPTICAL CHANNELS, not one.

The supervisor's V2.1 baseline is explicit that these must not be merged:

    Base / back surface        prescription
                               progressive / freeform component
                               optional low-order aspheric or SA component

    Front NSO microstructure   spatial-statistical optical modulation
                               scattering / phase / contrast redistribution
                               blue-noise / Poisson distribution
                               temporal asymmetry

NSO is NOT a DIMS/HAL lenslet add-power architecture, so the microstructure's
optical effect cannot be expressed as base-surface sag. An earlier version did
exactly that: it took the profile's `3-5-4D` label, treated it as a surface
spherical-aberration term, and folded it into the sag map. That conflated the
two channels and was wrong at the root.

The two profiles below are therefore separate objects with separate
manufacturing paths — the back surface goes to the freeform vendor, the front
microstructure to the microstructure vendor, and neither is derived from the
other.

Kept dependency-free so nothing about the optical design leaks in through an
import cycle, and so these dataclasses can be swapped for database rows later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

#: Zone labels of the three-zone NSO architecture, centre outward.
ZONE_LABELS = ("A", "B", "C")


@dataclass
class ZoneGeometry:
    """Manufacturable geometry for one microstructure zone.

    ``element_height_um`` is the DESIGN height. What the process actually
    leaves on the lens is smaller — hard-coat transfer alone takes a nominal
    3.0 µm structure down to roughly 1.8 µm — so the compensated height is
    computed separately in the manufacturing layer and never stored here.
    """

    zone: str
    element_diameter_um: float
    element_length_um: float        # equals diameter for round elements
    element_height_um: float
    fill_factor_pct: float
    inner_radius_mm: float
    outer_radius_mm: float

    @property
    def is_elongated(self) -> bool:
        return abs(self.element_length_um - self.element_diameter_um) > 1e-6


@dataclass
class BaseSurfaceProfile:
    """Posterior surface: prescription, freeform, and any low-order asphere.

    ``aspheric_sa_d`` is the surface's OWN spherical aberration. It is not the
    NSO modulation and must never be set from it. It defaults to zero, which
    says the honest thing: at present the back surface carries prescription
    only, and no aspheric component has been specified.
    """

    eye: str
    sphere: float
    cylinder: float
    axis: float
    aspheric_sa_d: float = 0.0
    #: Radius within which the aspheric term applies before the window tapers
    #: it. Outside the functional zone the surface is plain prescription.
    functional_zone_semi_diameter_mm: float = 10.0
    rolloff_blend_mm: float = 4.0


@dataclass
class NsoModulationProfile:
    """Anterior microstructure: spatial-statistical optical modulation.

    ``zone_targets_d`` holds the profile's nominal add targets — the numbers
    behind labels like ``3-5-4D``. They are OPTICAL TARGETS, not surface
    heights: converting a dioptric target straight into a structure height is
    the mistake the supervisor's V2.1 note warns against.
    """

    eye: str
    profile_tier: str                       # Low / Medium / High
    label: str                              # e.g. "3-5-4D"
    zone_targets_d: Dict[str, float]        # {"A": 3.0, "B": 5.0, "C": 4.0}
    zones: List[ZoneGeometry] = field(default_factory=list)
    #: Extra modulation applied to the temporal sector, percent.
    temporal_modulation_pct: float = 30.0
    spatial_jitter_deg: float = 8.0
    entropy: float = 0.0

    def zone(self, label: str) -> ZoneGeometry:
        for z in self.zones:
            if z.zone == label:
                return z
        raise KeyError(f"no zone {label!r} in this profile")

    @property
    def mean_fill_factor_pct(self) -> float:
        return sum(z.fill_factor_pct for z in self.zones) / len(self.zones)

    @property
    def peak_target_d(self) -> float:
        return max(self.zone_targets_d.values())


@dataclass
class DesignRecipe:
    """One eye's complete design: both channels. NEVER send this to a client."""

    eye: str
    axial_length: float
    base_surface: BaseSurfaceProfile
    nso_modulation: NsoModulationProfile
    profile_tier: str
    target_mtf_modulation: float
    entropy: float

    # -- convenience accessors -------------------------------------------- #
    # Named so the channel is always visible at the call site. There is
    # deliberately no plain ``sa_strength`` any more: that name is what let the
    # microstructure modulation be read as base-surface aberration.

    @property
    def nso_peak_target_d(self) -> float:
        """Peak zone add target of the microstructure channel, dioptres."""
        return self.nso_modulation.peak_target_d

    @property
    def base_aspheric_sa_d(self) -> float:
        """Spherical aberration of the BACK SURFACE only."""
        return self.base_surface.aspheric_sa_d

    @property
    def mean_fill_factor_pct(self) -> float:
        return self.nso_modulation.mean_fill_factor_pct
