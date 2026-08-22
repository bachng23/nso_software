"""
The design recipe — Design IP layer.

Kept in its own module with no dependencies so nothing about the optical design
leaks in through an import cycle, and so the dataclass can be swapped for a
database row later without touching the synthesis code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DesignRecipe:
    """Full per-eye optical recipe. NEVER send this to a client."""
    eye: str
    axial_length: float
    base_sphere: float
    base_cylinder: float
    base_axis: float
    sa_profile: str
    sa_strength: float            # D
    microstructure_diameter_um: float
    microstructure_height_um: float
    fill_factor_pct: float
    spatial_density_per_mm2: float
    spatial_jitter_deg: float
    temporal_nasal_ratio: float
    target_mtf_modulation: float
    zone_count: int
    entropy: float
    profile_tier: str
