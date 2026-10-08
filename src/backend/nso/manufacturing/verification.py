"""
Geometric verification of an as-manufactured lens.

Closes the ``Manufacturing Projection -> Verification`` link of the V2
architecture -- but only half of it.

WHAT THIS CHECKS: geometric conformance. Sag error, element height, element
position, decentration. Did the machine cut the shape that was sent?

WHAT IT DOES NOT CHECK: optical performance. MTF, through-focus response and
peripheral defocus are never measured. A lens that passes here can still miss
the performance the clinical layer predicted.

The function was originally called ``optical_verification``, which claimed the
second thing while doing only the first -- the most dangerous kind of wrong
name, because the reader has no reason to doubt it. Every result now carries
``optical_performance_verified: False`` so the gap is visible in the payload
and not only in this docstring.

Real optical verification needs a bench protocol and instrument specification
that have not been provided (ASSUMPTIONS.md P0-5).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from ..config import get_config
from ..ip import assert_no_design_leak
from ..engineering import ClinicalDigitalThread
from .registry import REGISTRY


def geometric_verification(
    design_id: str, measurements: Dict[str, float], manufacturing_id: str | None = None
) -> Dict[str, Any]:
    """Compare as-manufactured geometry against the acceptance window.

    The verdict covers shape conformance only. See the module docstring for
    what is deliberately not covered.
    """
    cfg = get_config()
    if not REGISTRY.known(design_id):
        raise KeyError(design_id)
    windows = {
        "sag_error_mm": cfg.sag_tolerance_mm,
        "element_height_error_mm": cfg.height_tolerance_mm,
        "element_position_error_mm": cfg.position_tolerance_mm,
        "decentration_mm": cfg.decentration_tolerance_mm,
    }

    checks = []
    for key, limit in windows.items():
        if key not in measurements:
            continue
        value = abs(float(measurements[key]))
        checks.append(
            {"check": key, "measured": round(value, 6), "limit": limit,
             "pass": value <= limit}
        )

    if not checks:
        verdict = "Incomplete"
    elif all(c["pass"] for c in checks):
        verdict = "Pass"
    else:
        verdict = "Fail"

    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Keep the measurements, not just the verdict: they are the only record of
    # what the process actually produced, and the raw material for
    # manufacturing compensation later (ASSUMPTIONS P2-10).
    REGISTRY.record_verification(
        design_id,
        {"type": "geometric", "verdict": verdict, "checks": checks,
         "manufacturing_id": manufacturing_id,
         "checked_at": checked_at},
    )
    if manufacturing_id:
        try:
            job = REGISTRY._store_impl.get_record("manufacturing", manufacturing_id)
            lot_id = job.get("lot_id")
            if lot_id:
                thread = ClinicalDigitalThread(REGISTRY._store_impl)
                for check in checks:
                    thread.record_qc(
                        lot_id=lot_id,
                        measurement_type=check["check"],
                        target=check["limit"],
                        actual=check["measured"],
                        unit="mm",
                        passed=check["pass"],
                        measurement_data={"verification_type": "geometric"},
                        actor="verification-station",
                    )
        except KeyError:
            # Legacy callers may verify by design without a governed lot. The
            # response remains valid but is not eligible for closed-loop ML.
            pass

    out = {
        "design_id": design_id,
        "manufacturing_id": manufacturing_id,
        "verification_type": "geometric",
        "verdict": verdict,
        "checks": checks,
        "checked_at": checked_at,
        # Stated in the payload, not just the docs: a Pass here says the shape
        # was cut correctly, never that the lens performs as predicted.
        "optical_performance_verified": False,
        "optical_performance_note": (
            "Geometric conformance only. MTF and through-focus performance were "
            "not measured; a bench protocol has not been defined."
        ),
    }
    assert_no_design_leak(out)
    return out
