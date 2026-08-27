"""
The IP boundary guard.

Screens a payload for Design IP before it crosses the network. Kept in its own
module so the rule has one home and every layer imports the same one.
"""

from __future__ import annotations

from typing import Any

# Key fragments belonging to the Design IP / Manufacturing IP layers.
FORBIDDEN_KEY_FRAGMENTS = (
    "sa_strength",
    "sa_profile",
    # Two-channel naming: the NSO modulation target and the base surface's own
    # aberration are both Design IP, and so is the per-zone geometry.
    "nso_peak_target",
    "nso_mean_fill",
    "nso_temporal",
    "nso_spatial",
    "nso_entropy",
    "aspheric_sa",
    "zone_target",
    "zone_height",
    "zone_diameter",
    "element_height",
    "element_diameter",
    "height_retention",
    "process_transfer",
    "microstructure",
    "fill_factor",
    "spatial_density",
    "jitter",
    "temporal_multiplier",
    "temporal_nasal",
    "toolpath",
    "surface_map",
    "freeform",
    "target_mtf",
    "mtf_reduction",
    "density",
    "coefficient",
    "recipe",
)

# Value fragments. Keys alone are not enough: a free-text field can carry the
# recipe just as effectively as a named parameter (ASSUMPTIONS.md P2-11).
FORBIDDEN_VALUE_FRAGMENTS = (
    "spherical aberration",
    "sa strength",
    "fill factor",
    "microstructure",
    "blue-noise",
    "toolpath",
)


class DesignLeakError(AssertionError):
    """Raised when Design IP would be serialized into a clinical payload."""


def assert_no_design_leak(payload: Any, _path: str = "clinical") -> None:
    """Recursively verify that no Design IP key or value is present."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            lowered = str(k).lower()
            for frag in FORBIDDEN_KEY_FRAGMENTS:
                if frag in lowered:
                    raise DesignLeakError(
                        f"Design IP key '{k}' would leak to the client at {_path}"
                    )
            assert_no_design_leak(v, f"{_path}.{k}")
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            assert_no_design_leak(v, f"{_path}[{i}]")
    elif isinstance(payload, str):
        lowered = payload.lower()
        for frag in FORBIDDEN_VALUE_FRAGMENTS:
            if frag in lowered:
                raise DesignLeakError(
                    f"Design IP phrase '{frag}' would leak to the client at {_path}"
                )
