"""
Manufacturing projection — geometry generation, one path per optical channel.

    base surface       -> sag map        -> freeform vendor
    NSO microstructure -> placement map  -> microstructure vendor

The two are generated independently and shipped to different vendors. Nothing
in the sag map is derived from the microstructure profile: the sag carries the
prescription and, if one is specified, the surface's own aspheric term. An
earlier version folded the microstructure's `3-5-4D` modulation target into the
sag as a spherical-aberration polynomial, which merged two channels that are
physically distinct.

Turning a recipe into coordinates is also what makes parameter abstraction
possible: a vendor receives sampled geometry, not the design language that
produced it.

SCOPE BOUNDARY: the projection stops at GEOMETRY, and at DESIGN geometry
specifically. Two further stages belong elsewhere:

  * process compensation -- hard coat alone takes a nominal 3.0 um structure
    down to roughly 1.8 um. Handled in ``compensation.py``, not here.
  * toolpath -- needs tool nose radius, feed rate, depth of cut, spindle speed,
    controller dialect and blank material. The vendor's CAM performs it.

    Design -> Sag / Placement -> Process compensation | -> CAM -> Toolpath
                    (this module)     (compensation.py) |    (vendor side)
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, List, Tuple

from ..config import get_config
from ..recipe import DesignRecipe, ZoneGeometry

_MASK64 = (1 << 64) - 1

#: Minimum rows per page. Conflict resolution needs a two-row margin either
#: side, so a one-row page hashes five rows to emit one.
MIN_ROWS_PER_PAGE = 16


# --------------------------------------------------------------------------- #
# Base surface — sag map
# --------------------------------------------------------------------------- #

def _smootherstep(t: float) -> float:
    """C2-continuous 0->1 ramp (6t^5 - 15t^4 + 10t^3).

    First and second derivatives vanish at both ends, so a blend built on it
    introduces no curvature step -- and therefore no defect ring -- where it
    starts or finishes.
    """
    t = max(0.0, min(t, 1.0))
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


def _window(r_mm: float, zone_semi: float, blend: float) -> float:
    """Bounded window W(r) for the higher-order term.

    The supervisor's V2.1 answer to "how should r^4 fade" is a (b)+(c) hybrid:
    the term exists only inside a defined functional zone and transitions
    smoothly at the zone boundary. It must not run unconstrained to the edge of
    a 65 mm blank -- a computed sag of several millimetres is a sign the model
    domain was defined wrongly, not a lens.

        SA_effective(r) = C4 * r^4 * W(r)

    The 4 mm blend width is an engineering default, not an NSO physical
    constant; it should be set from MTF/PSF results together with freeform
    feasibility (ASSUMPTIONS P0-3).
    """
    if r_mm <= zone_semi:
        return 1.0
    if blend <= 0.0:
        return 0.0
    return 1.0 - _smootherstep((r_mm - zone_semi) / blend)


def _sag_sphere(r_mm: float, power_d: float, lens_index: float) -> float:
    """Sag of a spherical surface of the given back-vertex power, in mm."""
    if abs(power_d) < 1e-9:
        return 0.0
    radius = (lens_index - 1.0) * 1000.0 / power_d   # mm
    inside = radius * radius - r_mm * r_mm
    if inside <= 0:
        return 0.0
    return radius - math.copysign(math.sqrt(inside), radius)


def surface_map(recipe: DesignRecipe) -> Dict[str, Any]:
    """Back-surface sag map z(r, theta) in millimetres.

    Carries the prescription and the surface's own aspheric term. The NSO
    microstructure modulation is NOT part of this map -- it travels on the
    front surface as a placement list.
    """
    cfg = get_config()
    base = recipe.base_surface
    semi = cfg.optic_zone_diameter_mm / 2.0
    zone_semi = base.functional_zone_semi_diameter_mm

    # Aspheric term of the BACK SURFACE, expanded as r^4 inside the functional
    # zone. Zero unless a surface asphere has been specified.
    k_sa = (
        base.aspheric_sa_d
        / (2.0 * (cfg.lens_index - 1.0) * 1000.0 * zone_semi * zone_semi)
    )

    axis_rad = math.radians(base.axis)
    points = []
    for i in range(cfg.surface_radial_samples):
        r = semi * i / (cfg.surface_radial_samples - 1)
        w = _window(r, zone_semi, base.rolloff_blend_mm)
        for j in range(cfg.surface_meridional_samples):
            theta = 2.0 * math.pi * j / cfg.surface_meridional_samples
            cyl_power = base.cylinder * math.sin(theta - axis_rad) ** 2
            z = (
                _sag_sphere(r, base.sphere + cyl_power, cfg.lens_index)
                + k_sa * r ** 4 * w
            )
            points.append([round(r, 4), round(math.degrees(theta), 2), round(z, 6)])

    return {
        "units": {"r": "mm", "theta": "deg", "z": "mm"},
        "optic_diameter_mm": cfg.optic_zone_diameter_mm,
        "sample_count": len(points),
        "points": points,
    }


# --------------------------------------------------------------------------- #
# Front microstructure — placement map, one lattice per zone
# --------------------------------------------------------------------------- #

def _element_area_mm2(zone: ZoneGeometry) -> float:
    """Footprint of one element. Elliptical, so zone C's 32 x 16 works."""
    a = zone.element_diameter_um / 2000.0
    b = zone.element_length_um / 2000.0
    return math.pi * a * b


def zone_density_per_mm2(zone: ZoneGeometry) -> float:
    """Areal density implied by the zone's fill factor and element size."""
    area = _element_area_mm2(zone)
    return (zone.fill_factor_pct / 100.0) / area if area > 0 else 0.0


def _zone_lattice(zone: ZoneGeometry):
    """Sampling grid for one zone.

    Each zone has its own element size and fill factor, so each needs its own
    Poisson radius and its own grid. Cell size is radius/sqrt(2), the standard
    choice: at most one accepted sample per cell, so conflicts only need a
    fixed neighbourhood.
    """
    cfg = get_config()
    density = zone_density_per_mm2(zone)
    radius = math.sqrt(0.7 / max(density, 1e-6))
    cell = radius / math.sqrt(2.0)
    span = 2.0 * zone.outer_radius_mm
    per_side = max(1, int(math.ceil(span / cell)))
    rows_per_page = max(
        MIN_ROWS_PER_PAGE, cfg.microstructure_page_size // max(per_side, 1)
    )
    return zone.outer_radius_mm, radius, cell, per_side, rows_per_page


def _zone_seed(recipe: DesignRecipe, zone: ZoneGeometry) -> int:
    """One 64-bit seed per (design, zone). Hashed once, not per cell."""
    key = (
        f"{recipe.eye}:{recipe.nso_modulation.label}:{zone.zone}"
        f":{zone.fill_factor_pct}:{zone.element_diameter_um}"
        f":{recipe.nso_modulation.spatial_jitter_deg}"
    )
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16)


def _mix64(x: int) -> int:
    """SplitMix64 finalizer: a fast, well-distributed integer hash.

    Deliberately not cryptographic. Nothing here resists inversion -- the
    coordinates go to the vendor anyway -- and SHA-256 per cell cost roughly
    thirty times as much for a property the design does not rely on.
    """
    z = (x + 0x9E3779B97F4A7C15) & _MASK64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return (z ^ (z >> 31)) & _MASK64


def _cell_sample(seed: int, gi: int, gj: int, cell: float, semi: float,
                 inner: float, outer: float):
    """Candidate sample for a cell: (x, y, priority), or None if out of zone."""
    h = _mix64(seed ^ _mix64((gi << 32) ^ (gj & 0xFFFFFFFF)))
    fx = ((h >> 8) & 0xFFFF) / 65535.0
    fy = ((h >> 24) & 0xFFFF) / 65535.0
    x = -semi + (gi + fx) * cell
    y = -semi + (gj + fy) * cell
    rr = x * x + y * y
    if rr > outer * outer or rr < inner * inner:
        return None
    return x, y, h & 0xFFFFFF


def _row_samples(seed, gi, cell, semi, per_side, inner, outer):
    if not 0 <= gi < per_side:
        return {}
    return {
        gj: s
        for gj in range(per_side)
        if (s := _cell_sample(seed, gi, gj, cell, semi, inner, outer)) is not None
    }


def _resolve_row(gi: int, band, radius: float):
    """Accepted samples in one grid row, after Poisson-disk conflict resolution.

    A sample is kept unless a higher-priority sample lies within the disk
    radius. Only the 5x5 neighbourhood can conflict, because a cell is
    radius/sqrt(2) across, so the test is local -- which is what keeps the map
    page-addressable, a property Bridson's sequential algorithm cannot offer.

    This is dart-throwing with a deterministic priority: it gives the
    minimum-distance guarantee and the blue-noise spectrum a jittered grid does
    not.
    """
    r2 = radius * radius
    out = []
    for gj, (x, y, priority) in band.get(gi, {}).items():
        blocked = False
        for row in range(gi - 2, gi + 3):
            neighbours = band.get(row)
            if not neighbours:
                continue
            for col in range(gj - 2, gj + 3):
                if row == gi and col == gj:
                    continue
                other = neighbours.get(col)
                if other is None:
                    continue
                ox, oy, other_priority = other
                if other_priority <= priority:
                    continue
                if (x - ox) ** 2 + (y - oy) ** 2 < r2:
                    blocked = True
                    break
            if blocked:
                break
        if not blocked:
            out.append((x, y))
    return out


def _page_plan(recipe: DesignRecipe) -> List[Tuple[ZoneGeometry, int]]:
    """Flat list of (zone, row band) pairs, in zone order.

    Zones have different densities and therefore different grids, so pagination
    runs zone by zone rather than over one global lattice. The plan is cheap to
    build -- no elements are generated -- which is what keeps any page
    reachable without walking the pages before it.
    """
    plan = []
    for zone in recipe.nso_modulation.zones:
        _semi, _radius, _cell, per_side, rows_per_page = _zone_lattice(zone)
        pages = (per_side + rows_per_page - 1) // rows_per_page
        plan.extend((zone, band) for band in range(pages))
    return plan


def microstructure_page_count(recipe: DesignRecipe) -> int:
    """Exact number of pages across all zones. Generates nothing."""
    return len(_page_plan(recipe))


def microstructure_element_count(recipe: DesignRecipe) -> int:
    """Estimated element count from each zone's fill factor and annulus area.

    Labelled an estimate wherever it is reported: the lattice is square and
    clipped to an annulus, so the true count differs by the packing fraction.
    Counting exactly means generating every element.
    """
    total = 0
    for zone in recipe.nso_modulation.zones:
        area = math.pi * (zone.outer_radius_mm ** 2 - zone.inner_radius_mm ** 2)
        total += int(zone_density_per_mm2(zone) * area)
    return total


def microstructure_map(recipe: DesignRecipe, page: int = 0) -> Dict[str, Any]:
    """Element placement list for one page.

    One page is a band of grid rows within one zone. Each element row is
    ``[x, y, diameter, length, height]`` in millimetres, so the elongated
    elements of zone C carry their own footprint.

    Heights here are DESIGN heights. Process compensation is applied later, in
    the manufacturing package, because how much a coating takes off is a
    property of the process and not of the design.
    """
    plan = _page_plan(recipe)
    total_pages = len(plan)
    if not 0 <= page < total_pages:
        return {
            "units": {"x": "mm", "y": "mm", "d": "mm", "l": "mm", "h": "mm"},
            "page": page, "total_pages": total_pages, "zone": None,
            "estimated_total_elements": microstructure_element_count(recipe),
            "returned": 0, "next_page": None, "elements": [],
        }

    zone, band_index = plan[page]
    semi, radius, cell, per_side, rows_per_page = _zone_lattice(zone)
    seed = _zone_seed(recipe, zone)

    first_row = band_index * rows_per_page
    last_row = min(first_row + rows_per_page, per_side)

    # Hash the page's rows plus the two-row conflict margin, once.
    band = {
        row: _row_samples(seed, row, cell, semi, per_side,
                          zone.inner_radius_mm, zone.outer_radius_mm)
        for row in range(first_row - 2, last_row + 2)
    }

    d_mm = round(zone.element_diameter_um / 1000.0, 5)
    l_mm = round(zone.element_length_um / 1000.0, 5)
    h_mm = round(zone.element_height_um / 1000.0, 5)

    elements = []
    for gi in range(first_row, last_row):
        for x, y in _resolve_row(gi, band, radius):
            elements.append([round(x, 4), round(y, 4), d_mm, l_mm, h_mm])

    return {
        "units": {"x": "mm", "y": "mm", "d": "mm", "l": "mm", "h": "mm"},
        "page": page,
        "total_pages": total_pages,
        "zone": zone.zone,
        "rows_per_page": rows_per_page,
        "estimated_total_elements": microstructure_element_count(recipe),
        "returned": len(elements),
        "next_page": page + 1 if page + 1 < total_pages else None,
        "elements": elements,
    }
