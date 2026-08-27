"""
Manufacturing compensation — design geometry to as-built geometry.

A process does not reproduce what the design asks for. Hard coating alone takes
a nominal 3.0 um structure down to roughly 1.8 um of effective geometry, so a
lens cut to the design numbers arrives short of its optical target.

The supervisor's V2.1 note puts this squarely in its own layer:

    Design geometry -> [ process transfer model ] -> manufacturing geometry

That placement matters. The 3.0 -> 1.8 um shortfall is a property of the
coating line, not of the fitting algorithm, so the fitting engine must not be
tuned to hide it. Keeping compensation separate means:

  * changing vendor or coating changes one transfer model, not the design rules
  * the design intent stays visible and auditable next to what was actually cut
  * a model trained on clinical outcomes learns from design geometry, which is
    what the optics were specified as, rather than from one line's losses

Every number here is a PLACEHOLDER until the manufacturer supplies measured
transfer data (ASSUMPTIONS P0-4, P1-22).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ..config import get_config
from ..recipe import DesignRecipe, ZoneGeometry


@dataclass(frozen=True)
class ProcessTransfer:
    """How much of a design feature survives the process.

    ``height_retention`` is the fraction of design height left after coating
    and replication. The observed 3.0 um -> 1.8 um is a retention of 0.6, which
    is the default here.
    """

    name: str
    height_retention: float
    diameter_growth: float          # lateral spreading, fraction
    fill_factor_growth: float       # follows from diameter growth
    minimum_height_um: float        # below this the feature does not form

    def compensated_height_um(self, design_height_um: float) -> float:
        """Height to CUT so the finished lens ends up at the design height.

        Pre-compensation, not measurement: the machine is asked for more so the
        process leaves the right amount.
        """
        if self.height_retention <= 0:
            return design_height_um
        return design_height_um / self.height_retention

    def as_built_height_um(self, cut_height_um: float) -> float:
        """What the finished lens actually carries after the process."""
        return cut_height_um * self.height_retention


def active_transfer() -> ProcessTransfer:
    cfg = get_config()
    return ProcessTransfer(
        name=cfg.process_transfer_name,
        height_retention=cfg.process_height_retention,
        diameter_growth=cfg.process_diameter_growth,
        fill_factor_growth=cfg.process_fill_factor_growth,
        minimum_height_um=cfg.process_minimum_height_um,
    )


def compensate_zone(zone: ZoneGeometry, transfer: ProcessTransfer) -> Dict[str, Any]:
    """One zone's design geometry alongside what the machine should cut."""
    cut_height = transfer.compensated_height_um(zone.element_height_um)
    cut_diameter = zone.element_diameter_um / (1.0 + transfer.diameter_growth)
    cut_length = zone.element_length_um / (1.0 + transfer.diameter_growth)
    cut_fill = zone.fill_factor_pct / (1.0 + transfer.fill_factor_growth)

    return {
        "zone": zone.zone,
        "design": {
            "element_height_um": zone.element_height_um,
            "element_diameter_um": zone.element_diameter_um,
            "element_length_um": zone.element_length_um,
            "fill_factor_pct": zone.fill_factor_pct,
        },
        "cut": {
            "element_height_um": round(cut_height, 3),
            "element_diameter_um": round(cut_diameter, 2),
            "element_length_um": round(cut_length, 2),
            "fill_factor_pct": round(cut_fill, 2),
        },
        "predicted_as_built": {
            "element_height_um": round(
                transfer.as_built_height_um(cut_height), 3),
        },
        # A design the process cannot form at all is a design problem, and the
        # vendor should see that rather than silently cutting something else.
        "formable": cut_height >= transfer.minimum_height_um,
    }


def compensation_report(recipe: DesignRecipe) -> Dict[str, Any]:
    """Design vs cut geometry for every zone, with the transfer model named.

    Server-side only: it contains the design geometry and is Design IP.
    """
    transfer = active_transfer()
    zones: List[Dict[str, Any]] = [
        compensate_zone(z, transfer) for z in recipe.nso_modulation.zones
    ]
    return {
        "transfer_model": {
            "name": transfer.name,
            "height_retention": transfer.height_retention,
            "diameter_growth": transfer.diameter_growth,
            "calibrated": False,
            "note": (
                "Placeholder transfer model. The 0.6 height retention comes "
                "from a single reported observation (3.0 um design leaving "
                "about 1.8 um after hard coat) and has not been measured "
                "across the process window."
            ),
        },
        "zones": zones,
        "all_formable": all(z["formable"] for z in zones),
    }


def compensated_placement(recipe: DesignRecipe, page: int = 0) -> Dict[str, Any]:
    """A placement page with cut heights substituted for design heights.

    What the vendor receives. The compensation is folded into the coordinates
    themselves rather than shipped as a side table, because a side table would
    have to name zones and design heights — exactly the design language the
    package exists to withhold.
    """
    from .geometry import microstructure_map     # local: avoids an import cycle

    transfer = active_transfer()
    page_data = microstructure_map(recipe, page=page)

    elements = []
    for x, y, d_mm, l_mm, h_mm in page_data["elements"]:
        elements.append([
            x, y,
            round(d_mm / (1.0 + transfer.diameter_growth), 5),
            round(l_mm / (1.0 + transfer.diameter_growth), 5),
            round(transfer.compensated_height_um(h_mm * 1000.0) / 1000.0, 5),
        ])

    return {
        **{k: v for k, v in page_data.items() if k not in ("elements", "zone")},
        "geometry": "as_cut",
        "elements": elements,
    }
