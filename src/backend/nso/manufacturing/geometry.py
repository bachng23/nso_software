"""
Manufacturing projection — geometry generation.

Turns a DesignRecipe into coordinates. This is what makes parameter abstraction
possible: a vendor receives sampled geometry, not the design language that
produced it.

SCOPE BOUNDARY: the projection stops at GEOMETRY. Toolpath generation needs
machine-specific inputs this system does not have -- tool nose radius, feed
rate, depth of cut, spindle speed, controller dialect, blank material and mould
shrinkage compensation. The vendor's CAM performs that step.

    Design -> Surface Map -> Microstructure Map | -> CAM -> Toolpath -> G-code
                                 (this system)  |        (vendor side)

Every lens-construction constant used here is a PLACEHOLDER (ASSUMPTIONS.md
P0-1..P0-3). They live in ``EngineConfig`` so replacing them with the real lens
specification is a config change.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict

from ..config import get_config
from ..recipe import DesignRecipe


def _smootherstep(t: float) -> float:
    """C2-continuous 0->1 ramp (6t^5 - 15t^4 + 10t^3).

    First and second derivatives vanish at both ends, which is the property
    that matters here: a blend built on it introduces no curvature step, and
    therefore no defect ring, where it starts or finishes.
    """
    t = max(0.0, min(t, 1.0))
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


def _higher_order_sag(r_mm: float, k_sa: float, r0: float, blend: float) -> float:
    """The design's higher-order sag term, tapered to a plateau.

    Inside ``r0`` the term is the plain r^4 curve. Over ``[r0, r0 + blend]`` it
    blends into the constant it reaches at ``r0`` and stays there.

    Why blend at all: extrapolating r^4 to the lens edge gives millimetres of
    sag no spectacle lens has, but clamping it at ``r0`` leaves a second-
    derivative discontinuity -- a ring of abrupt curvature change at exactly
    that radius. Both are wrong; the blend is wrong in a way that does not put
    a visible artefact on the lens.
    """
    plateau = k_sa * r0 ** 4
    if r_mm <= r0:
        return k_sa * r_mm ** 4
    if blend <= 0.0:
        return plateau
    w = _smootherstep((r_mm - r0) / blend)
    return (1.0 - w) * k_sa * r_mm ** 4 + w * plateau


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

    CONVENTION (unvalidated, ASSUMPTIONS P0-2): a peripheral add of
    ``sa_strength`` dioptres at the reference semi-diameter is expanded as an
    r^4 term scaled so the added sag at that radius matches the add.

    Beyond the reference radius the term tapers into a plateau over
    ``CONFIG.sa_rolloff_blend_mm`` using a C2-continuous blend, so the emitted
    surface has no curvature step (P0-3). Which roll-off the real design uses
    is still unspecified; this one is merely smooth.
    """
    cfg = get_config()
    semi = cfg.optic_zone_diameter_mm / 2.0
    r0 = cfg.sa_reference_semi_diameter_mm
    k_sa = recipe.sa_strength / (2.0 * (cfg.lens_index - 1.0) * 1000.0 * r0 * r0)

    axis_rad = math.radians(recipe.base_axis)
    points = []
    for i in range(cfg.surface_radial_samples):
        r = semi * i / (cfg.surface_radial_samples - 1)
        for j in range(cfg.surface_meridional_samples):
            theta = 2.0 * math.pi * j / cfg.surface_meridional_samples
            cyl_power = recipe.base_cylinder * math.sin(theta - axis_rad) ** 2
            z = (
                _sag_sphere(r, recipe.base_sphere + cyl_power, cfg.lens_index)
                + _higher_order_sag(r, k_sa, r0, cfg.sa_rolloff_blend_mm)
            )
            points.append([round(r, 4), round(math.degrees(theta), 2), round(z, 6)])

    return {
        "units": {"r": "mm", "theta": "deg", "z": "mm"},
        "optic_diameter_mm": cfg.optic_zone_diameter_mm,
        "sample_count": len(points),
        "points": points,
    }


def _lattice(recipe: DesignRecipe):
    """Sampling grid for the element placement.

    Cell size is the Poisson-disk radius over sqrt(2), the standard choice: it
    guarantees at most one accepted sample per cell, so conflicts only need to
    be checked against a fixed neighbourhood.
    """
    cfg = get_config()
    semi = cfg.optic_zone_diameter_mm / 2.0
    # Radius that yields the requested areal density for a Poisson-disk
    # distribution. Maximal packing puts ~0.7 samples per r^2; the constant
    # folds that in so density comes out close to the requested value.
    radius = math.sqrt(0.7 / max(recipe.spatial_density_per_mm2, 1e-6))
    cell = radius / math.sqrt(2.0)
    per_side = max(1, int(math.ceil(cfg.optic_zone_diameter_mm / cell)))
    # Rows per page. The floor matters: conflict resolution needs a two-row
    # margin either side, so a one-row page hashes five rows to emit one. A
    # wider band amortizes that, at the cost of pages that are larger than the
    # nominal page size.
    rows_per_page = max(
        MIN_ROWS_PER_PAGE, cfg.microstructure_page_size // max(per_side, 1)
    )
    return semi, radius, cell, per_side, rows_per_page


_MASK64 = (1 << 64) - 1


def _design_seed(recipe: DesignRecipe) -> int:
    """One 64-bit seed per design. Hashed once, not per cell."""
    key = (
        f"{recipe.eye}:{recipe.sa_strength}:{recipe.spatial_density_per_mm2}"
        f":{recipe.spatial_jitter_deg}"
    )
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16)


def _mix64(x: int) -> int:
    """SplitMix64 finalizer: a fast, well-distributed integer hash.

    Deliberately not a cryptographic hash. Nothing here needs to resist
    inversion -- the emitted coordinates are handed to the vendor anyway -- and
    running SHA-256 per cell cost roughly thirty times as much for a property
    the design does not rely on.
    """
    z = (x + 0x9E3779B97F4A7C15) & _MASK64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return (z ^ (z >> 31)) & _MASK64


def _cell_hash(seed: int, gi: int, gj: int) -> int:
    """Stable hash for one grid cell of one design.

    Everything about a cell -- whether it holds a sample, where, and its
    priority -- comes from this hash, which is what makes the map both
    reproducible and randomly addressable.
    """
    return _mix64(seed ^ _mix64((gi << 32) ^ (gj & 0xFFFFFFFF)))


def _cell_sample(seed: int, gi: int, gj: int, cell: float, semi: float):
    """The candidate sample for a cell: (x, y, priority), or None if outside.

    One candidate per cell, placed uniformly within it. The priority decides
    who survives a conflict, and because it is a hash rather than an iteration
    order, any cell can be resolved without visiting the ones before it.
    """
    h = _cell_hash(seed, gi, gj)
    fx = ((h >> 8) & 0xFFFF) / 65535.0
    fy = ((h >> 24) & 0xFFFF) / 65535.0
    x = -semi + (gi + fx) * cell
    y = -semi + (gj + fy) * cell
    if x * x + y * y > semi * semi:
        return None
    return x, y, h & 0xFFFFFF


def _row_samples(seed: int, gi: int, cell: float, semi: float, per_side: int):
    """Candidate samples for one whole row, indexed by column."""
    if not 0 <= gi < per_side:
        return {}
    return {
        gj: sample
        for gj in range(per_side)
        if (sample := _cell_sample(seed, gi, gj, cell, semi)) is not None
    }


MIN_ROWS_PER_PAGE = 16


def _resolve_row(gi: int, band, radius: float):
    """Accepted samples in one grid row, after conflict resolution.

    A sample is kept unless a higher-priority sample lies within the disk
    radius. Only the 5x5 neighbourhood can conflict, because a cell is
    radius/sqrt(2) across, so the test is local -- which is what keeps the map
    page-addressable, a property Bridson's sequential algorithm cannot offer.

    This is dart-throwing with a deterministic priority: it gives the
    minimum-distance guarantee and the blue-noise spectrum that a jittered grid
    does not (ASSUMPTIONS P2-8).

    ``band`` holds the already-hashed candidate rows, shared across the whole
    page: hashing each row once per page rather than once per row it appears in
    is what keeps a full pull to seconds rather than minutes.
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
                    continue      # lower priority yields to us
                if (x - ox) ** 2 + (y - oy) ** 2 < r2:
                    blocked = True
                    break
            if blocked:
                break
        if not blocked:
            out.append((x, y))
    return out


def microstructure_page_count(recipe: DesignRecipe) -> int:
    """Exact number of pages. Cheap -- no elements are generated."""
    *_rest, per_side, rows_per_page = _lattice(recipe)
    return (per_side + rows_per_page - 1) // rows_per_page


def microstructure_element_count(recipe: DesignRecipe) -> int:
    """Estimated element count from areal density over the optic zone.

    Labelled an estimate wherever it is reported: the emitted lattice is a
    square grid clipped to a circle, so the true count differs by the packing
    fraction. Counting exactly means generating every element, which is not
    worth doing on an API call.
    """
    cfg = get_config()
    area = math.pi * (cfg.optic_zone_diameter_mm / 2.0) ** 2
    return int(recipe.spatial_density_per_mm2 * area)


def microstructure_map(recipe: DesignRecipe, page: int = 0) -> Dict[str, Any]:
    """Element placement list: one (x, y, diameter, height) row per element.

    One page is a band of grid rows. Placement is seeded from the design itself,
    so the map is reproducible for a given Design ID; the vendor sees a cloud of
    positions, not the rule that generated it.

    Placement is a true Poisson-disk distribution: no two elements fall closer
    than the disk radius, which is what gives the blue-noise spectrum. A
    jittered grid, which this used to be, keeps the grid's spectral peaks and
    would diffract differently.
    """
    semi, radius, cell, per_side, rows_per_page = _lattice(recipe)
    total_pages = microstructure_page_count(recipe)

    d_mm = round(recipe.microstructure_diameter_um / 1000.0, 5)
    h_mm = round(recipe.microstructure_height_um / 1000.0, 5)

    first_row = page * rows_per_page
    last_row = min(first_row + rows_per_page, per_side)

    # Hash the page's rows plus the two-row conflict margin, once.
    seed = _design_seed(recipe)
    band = {
        row: _row_samples(seed, row, cell, semi, per_side)
        for row in range(first_row - 2, last_row + 2)
    }

    elements = []
    for gi in range(first_row, last_row):
        for x, y in _resolve_row(gi, band, radius):
            elements.append([round(x, 4), round(y, 4), d_mm, h_mm])

    return {
        "units": {"x": "mm", "y": "mm", "d": "mm", "h": "mm"},
        "page": page,
        "page_size": get_config().microstructure_page_size,
        "total_pages": total_pages,
        "rows_per_page": rows_per_page,
        "estimated_total_elements": microstructure_element_count(recipe),
        "returned": len(elements),
        "next_page": page + 1 if page + 1 < total_pages else None,
        "elements": elements,
    }
