"""
Design ID -> recipe store, and the segmented manufacturing release.

``DesignStore`` is the persistence seam. The in-memory implementation is what
runs today; ASSUMPTIONS.md P2-1 records why that blocks real deployment (a
restart invalidates every Design ID). Swapping in a database means implementing
this protocol, not editing the registry.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from ..config import get_config
from .compensation import compensated_placement
from .geometry import surface_map
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

OEM_CAPABILITIES = {
    "default": {"version": "1.0", "segments": list(SEGMENT_CODES), "qc_required": True},
    "front_surface": {"version": "1.0", "segments": ["FS"], "qc_required": True},
    "back_surface": {"version": "1.0", "segments": ["BS"], "qc_required": True},
    "assembly": {"version": "1.0", "segments": ["AV"], "qc_required": True},
    "internal": {"version": "1.0", "segments": list(SEGMENT_CODES), "qc_required": True},
}

VAULT_FIELDS = frozenset({
    "recipes", "candidates", "base_surface_optical_profile",
    "nso_spatial_modulation_profile",
})


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
        created = not self.known(design_id)
        vault = {key: payload[key] for key in VAULT_FIELDS if key in payload}
        metadata = {key: value for key, value in payload.items() if key not in VAULT_FIELDS}
        vault_id = f"VLT-{hashlib.sha256(design_id.encode()).hexdigest()[:24].upper()}"
        integrity_hash = hashlib.sha256(
            json.dumps(vault, sort_keys=True, default=str).encode()
        ).hexdigest()
        self._store_impl.put_record("vault", vault_id, {
            **vault,
            "design_id": design_id,
            "integrity_hash": integrity_hash,
            "access_class": "restricted_design_ip",
        })
        self._store_impl.put(design_id, {**metadata, "vault_id": vault_id})
        if created:
            self._audit("design_generated", design_id, metadata.get("revision", 0))

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
        metadata = self._store_impl.get(design_id)
        vault = self._store_impl.get_record("vault", metadata["vault_id"])
        entry = {
            **metadata,
            **{key: vault[key] for key in VAULT_FIELDS if key in vault},
            "jobs": [r["job_id"] for r in self._store_impl.related("manufacturing", design_id)],
            "verifications": [
                {k: v for k, v in record.items() if k != "record_id"}
                for record in self._store_impl.related("verification", design_id)
            ],
        }
        revisions = self._store_impl.related("revision_metadata", design_id)
        if revisions:
            entry.update({
                key: revisions[-1][key]
                for key in ("previous_design_id", "revision")
                if key in revisions[-1]
            })
        return entry

    def _audit(
        self, action: str, object_id: str, version: int | str = 0,
        *, actor: str = "system", result: str = "success",
        object_type: str = "design",
    ) -> None:
        self._store_impl.append_audit({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "actor": actor,
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "version": version,
            "result": result,
        })

    def _audit_records(self, object_id: str | None = None) -> list[Dict[str, Any]]:
        return self._store_impl.audits(object_id)

    def _record_domain(self, domain: str, record_id: str, payload: Dict[str, Any]) -> bool:
        try:
            self._store_impl.get_record(domain, record_id)
            created = False
        except KeyError:
            created = True
        self._store_impl.put_record(domain, record_id, payload)
        if created and domain in {"patient", "clinical_dataset"}:
            self._audit(
                "patient_profile_created" if domain == "patient" else "clinical_dataset_created",
                record_id,
                object_type=domain,
            )
        return created

    def _domain_record(self, domain: str, record_id: str) -> Dict[str, Any]:
        return self._store_impl.get_record(domain, record_id)

    def _record_outcome(
        self, outcome_id: str, design_id: str, payload: Dict[str, Any]
    ) -> None:
        self._store_impl.put_record("outcome", outcome_id, {
            "outcome_id": outcome_id,
            "design_id": design_id,
            "patient_id": payload.get("patient_id"),
            "raw_record_id": f"{outcome_id}:raw" if "raw" in payload else None,
            "derived_record_id": f"{outcome_id}:derived",
        })
        if "raw" in payload:
            self._store_impl.put_record("outcome_raw", f"{outcome_id}:raw", payload["raw"])
        self._store_impl.put_record(
            "outcome_derived", f"{outcome_id}:derived", payload.get("derived", {})
        )
        exposure = payload.get("derived", {}).get("exposure")
        if exposure:
            self._store_impl.append_related("exposure", design_id, exposure)
        self._audit("followup_recorded", design_id)

    # -- explicit updates ------------------------------------------------- #

    def record_job(self, design_id: str, job_id: str) -> None:
        self._store_impl.append_related("manufacturing", design_id, {"job_id": job_id})
        self._audit("manufacturing_projection_created", design_id)

    def record_verification(self, design_id: str, record: Dict[str, Any]) -> None:
        self._store_impl.append_related("verification", design_id, record)
        self._audit("qc_uploaded", design_id)

    def link_revision(self, design_id: str, previous_design_id: str) -> int:
        """Mark a design as the successor of another. Returns the new revision."""
        # Kept for compatibility; immutable revisions must be created with a
        # new ID, so an already-registered baseline is never rewritten.
        previous = self._get(previous_design_id)
        revision = int(previous.get("revision", 0)) + 1
        if not self.known(design_id):
            raise KeyError(design_id)
        self._store_impl.append_related("revision_metadata", design_id, {
            "previous_design_id": previous_design_id,
            "revision": revision,
        })
        return revision

    def _approve(self, design_id: str, actor: str = "clinician") -> Dict[str, Any]:
        entry = self._get(design_id)
        approval = {
            "design_id": design_id,
            "clinical_dataset_id": entry.get("clinical_dataset_id"),
            "status": "Approved",
            "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "actor": actor,
        }
        existing = self._store_impl.related("approval", design_id)
        if existing:
            return existing[-1]
        self._store_impl.append_related("approval", design_id, approval)
        self._audit("design_approved", design_id, entry.get("revision", 0), actor=actor)
        return approval

    def _is_approved(self, design_id: str) -> bool:
        return bool(self._store_impl.related("approval", design_id))

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
        job = {
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
        self._store_impl.put_record("manufacturing", job_id, job)
        return job

    def manufacturing_segments(
        self, design_id: str, segment: str, page: int = 0,
        *, oem_id: str = "default", capability_version: str = "1.0",
    ) -> Dict[str, Any]:
        """Release ONE segment of the manufacturing projection to one vendor.

        Parameter abstraction: every package contains SAMPLED GEOMETRY, never
        design parameters. A vendor can machine the surface but cannot read off
        the optical language behind it. Recovering it would mean solving the
        inverse problem from coordinates, i.e. redoing the research.
        """
        cfg = get_config()
        entry = self._get(design_id)
        capability = OEM_CAPABILITIES.get(oem_id)
        if capability is None:
            raise ValueError(f"unknown OEM: {oem_id}")
        if capability_version != capability["version"]:
            raise ValueError("unsupported OEM capability version")
        if segment not in capability["segments"]:
            raise ValueError("OEM capability does not support this segment")
        self._store_impl.put_record(
            "oem_capability", f"{oem_id}:{capability_version}",
            {"oem_id": oem_id, **capability},
        )
        recipes = {r.eye: r for r in entry["recipes"]}

        if segment == "FS":
            # Coordinates carrying the heights to CUT, not the design heights.
            # The process removes roughly 40% of the height, so cutting the
            # design value would leave the lens short of its optical target.
            #
            # Compensation is applied to each element rather than shipped as a
            # separate table: a table would name zones and design heights,
            # which is the design language this package exists to withhold.
            package = {
                "package_type": "front_surface_geometry",
                "format": "element_placement/v2",
                "eyes": {eye: compensated_placement(r, page=page)
                         for eye, r in recipes.items()},
            }
            return self._package_envelope(design_id, segment, package, oem_id, capability_version)
        if segment == "BS":
            package = {
                "package_type": "back_surface_geometry",
                "format": "sag_map/v1",
                "eyes": {eye: surface_map(r) for eye, r in recipes.items()},
            }
            return self._package_envelope(design_id, segment, package, oem_id, capability_version)
        if segment == "AV":
            # Verification gets pass/fail windows, not the target that defines
            # them -- an inspector can accept or reject a lens without learning
            # what optical performance the design was aiming for.
            package = {
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
            return self._package_envelope(design_id, segment, package, oem_id, capability_version)
        raise ValueError(f"unknown segment: {segment}")

    def _package_envelope(
        self, design_id: str, segment: str, package: Dict[str, Any],
        oem_id: str, capability_version: str,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        self._audit("oem_package_accessed", design_id, actor=oem_id)
        return {
            **package,
            "design_reference": design_id,
            "segment": segment,
            "oem_id": oem_id,
            "oem_capability_version": capability_version,
            "projection_version": "2.1",
            "issued_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + timedelta(hours=24)).isoformat(timespec="seconds"),
        }


REGISTRY = DesignRegistry()
