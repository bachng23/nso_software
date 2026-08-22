"""
Design ID -> recipe store, and the segmented manufacturing release.

``DesignStore`` is the persistence seam. The in-memory implementation is what
runs today; ASSUMPTIONS.md P2-1 records why that blocks real deployment (a
restart invalidates every Design ID). Swapping in a database means implementing
this protocol, not editing the registry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from ..config import get_config
from .geometry import microstructure_map, surface_map
from .store import DesignStore, default_store

# Opaque manufacturing segment codes. Their meaning lives in the vendor
# contract, not in any payload the clinic or the browser ever sees.
SEGMENT_CODES = ("FS", "BS", "AV")

# Which segments a vendor role may pull. One key per role, so a single leaked
# credential cannot assemble the whole design (ASSUMPTIONS P2-3).
VENDOR_SEGMENTS = {
    "front_surface": {"FS"},
    "back_surface": {"BS"},
    "assembly": {"AV"},
    # Reserved for internal QA. Never issue this to an external vendor.
    "internal": set(SEGMENT_CODES),
}


class DesignRegistry:
    """Design lookup plus the only sanctioned read paths out of the IP layer.

    There is deliberately no method that returns a complete recipe to a caller:
    the manufacturing paths emit geometry, and nothing else is exposed.
    """

    def __init__(self, store: DesignStore | None = None) -> None:
        self._store_impl: DesignStore = store or default_store()

    @property
    def backend(self) -> str:
        return type(self._store_impl).__name__

    def register(self, design_id: str, payload: Dict[str, Any]) -> None:
        self._store_impl.put(design_id, payload)

    def known(self, design_id: str) -> bool:
        return self._store_impl.has(design_id)

    def _get(self, design_id: str) -> Dict[str, Any]:
        """Read one entry.

        Against a database this returns a COPY, so mutating it changes nothing
        until it is written back. Every mutation therefore goes through one of
        the explicit update methods below rather than editing the dict --
        in-place mutation used to work with the dict store and would silently
        stop working here, which is exactly the sort of bug a persistence swap
        introduces.
        """
        return self._store_impl.get(design_id)

    # -- explicit updates ------------------------------------------------- #

    def record_job(self, design_id: str, job_id: str) -> None:
        entry = self._get(design_id)
        entry.setdefault("jobs", []).append(job_id)
        self._store_impl.put(design_id, entry)

    def record_verification(self, design_id: str, record: Dict[str, Any]) -> None:
        entry = self._get(design_id)
        entry.setdefault("verifications", []).append(record)
        self._store_impl.put(design_id, entry)

    def link_revision(self, design_id: str, previous_design_id: str) -> int:
        """Mark a design as the successor of another. Returns the new revision."""
        entry = self._get(design_id)
        previous = self._get(previous_design_id)
        entry["previous_design_id"] = previous_design_id
        entry["revision"] = previous.get("revision", 1) + 1
        self._store_impl.put(design_id, entry)
        return entry["revision"]

    def submit_to_manufacturing(
        self, design_id: str, site: str | None = None
    ) -> Dict[str, Any]:
        """Create a manufacturing job. Returns a job handle, never a recipe."""
        entry = self._get(design_id)
        cfg = get_config()
        # The serial comes from the store, so two processes -- or two workers
        # of one server -- can never be handed the same manufacturing serial.
        serial = self._store_impl.next_job_serial()
        revision = entry.get("revision", 1)
        job_id = (
            f"NSO-{datetime.now(timezone.utc):%y}-{site or cfg.default_site}"
            f"-{serial:06d}-R{revision:02d}"
        )
        self.record_job(design_id, job_id)
        return {
            "job_id": job_id,
            "design_id": design_id,
            "status": "Submitted",
            "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # Segmented release: no single vendor receives the whole design, and
            # the clinic-facing response names opaque segment codes rather than
            # process steps -- "what is being made" is itself know-how.
            "segments": [
                {"segment": code, "package": f"{job_id}-{code}", "status": "Queued"}
                for code in SEGMENT_CODES
            ],
        }

    def manufacturing_segments(
        self, design_id: str, segment: str, page: int = 0
    ) -> Dict[str, Any]:
        """Release ONE segment of the manufacturing projection to one vendor.

        Parameter abstraction: every package contains SAMPLED GEOMETRY, never
        design parameters. A vendor can machine the surface but cannot read off
        the optical language behind it. Recovering it would mean solving the
        inverse problem from coordinates, i.e. redoing the research.
        """
        cfg = get_config()
        entry = self._get(design_id)
        recipes = {r.eye: r for r in entry["recipes"]}

        if segment == "FS":
            return {
                "package_type": "front_surface_geometry",
                "format": "element_placement/v1",
                "eyes": {eye: microstructure_map(r, page=page)
                         for eye, r in recipes.items()},
            }
        if segment == "BS":
            return {
                "package_type": "back_surface_geometry",
                "format": "sag_map/v1",
                "eyes": {eye: surface_map(r) for eye, r in recipes.items()},
            }
        if segment == "AV":
            # Verification gets pass/fail windows, not the target that defines
            # them -- an inspector can accept or reject a lens without learning
            # what optical performance the design was aiming for.
            return {
                "package_type": "verification_limits",
                "format": "acceptance_window/v1",
                "eyes": {
                    eye: {
                        "sag_tolerance_mm": cfg.sag_tolerance_mm,
                        "element_height_tolerance_mm": cfg.height_tolerance_mm,
                        "element_position_tolerance_mm": cfg.position_tolerance_mm,
                        "decentration_tolerance_mm": cfg.decentration_tolerance_mm,
                    }
                    for eye in recipes
                },
            }
        raise ValueError(f"unknown segment: {segment}")


REGISTRY = DesignRegistry()
