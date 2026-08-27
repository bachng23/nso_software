"""
Contrast sensitivity: a device-independent representation.

The V2.1 baseline asks for two things here, and both are about not hard-coding
one instrument into the engine:

  * do not assume the spatial frequencies. An earlier version fixed them at
    1.5 / 6 / 18 cpd, which silently made every AUC and slope wrong for any
    clinic using a different protocol.
  * do not permanently map Low / Mid / High onto 45 / 70 / 90. That mapping is
    a fallback for when nothing was measured, not a measurement.

So a measurement carries its own frequencies and its own scale, and the AI
layer consumes a normalized form. Swapping instruments then changes the stored
protocol, not the engine.

The stored schema is::

    device
    test_protocol
    spatial_frequency_cpd[]
    raw_sensitivity[]
    logCS[]
    normalization_reference
    AUC
    slope
    centroid

``logCS`` is the common currency: most instruments report log contrast
sensitivity or something convertible to it, and it is the scale on which AUC
and slope have their usual meaning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

#: Peak log contrast sensitivity of a healthy young adult, used to normalize
#: a curve onto 0-1. A reference, not a limit: values above it are possible.
DEFAULT_NORMALIZATION_REFERENCE = 2.0

#: Frequencies assumed when a clinic supplies three values without saying at
#: which frequencies they were taken. Recorded on the measurement so the
#: assumption travels with the data instead of hiding in the engine.
ASSUMED_FREQUENCIES_CPD = (1.5, 6.0, 18.0)


@dataclass
class CsfMeasurement:
    """One contrast-sensitivity measurement, with its own protocol attached."""

    spatial_frequency_cpd: List[float]
    #: Either raw sensitivity (1/contrast threshold) or logCS; whichever the
    #: instrument reports. ``logCS`` is derived if only raw is given.
    raw_sensitivity: Optional[List[float]] = None
    log_cs: Optional[List[float]] = None

    device: str = "unspecified"
    test_protocol: str = "unspecified"
    normalization_reference: float = DEFAULT_NORMALIZATION_REFERENCE
    #: True when the frequencies were assumed rather than reported.
    frequencies_assumed: bool = False

    def __post_init__(self) -> None:
        if self.log_cs is None and self.raw_sensitivity is not None:
            self.log_cs = [
                math.log10(v) if v > 0 else 0.0 for v in self.raw_sensitivity
            ]
        if self.raw_sensitivity is None and self.log_cs is not None:
            self.raw_sensitivity = [10.0 ** v for v in self.log_cs]

    # ------------------------------------------------------------------ #

    @property
    def is_usable(self) -> bool:
        return bool(self.log_cs) and len(self.log_cs) == len(
            self.spatial_frequency_cpd) and len(self.log_cs) >= 2

    def normalized(self) -> List[float]:
        """The curve on 0-1, against the normalization reference.

        This is what the AI layer consumes. Two clinics using different
        instruments produce comparable numbers here, which is the whole point
        of storing the protocol rather than a bare score.
        """
        if not self.is_usable:
            return []
        ref = self.normalization_reference or DEFAULT_NORMALIZATION_REFERENCE
        return [max(0.0, min(v / ref, 1.0)) for v in self.log_cs]

    def quality_0_100(self) -> Optional[float]:
        """Mean normalized sensitivity, 0-100.

        The single number the rest of the engine still wants. Derived from the
        measured curve rather than from a Low/Mid/High label.
        """
        curve = self.normalized()
        return round(100.0 * sum(curve) / len(curve), 1) if curve else None

    # ------------------------------------------------------------------ #

    def auc(self) -> Optional[float]:
        """Area under the log-frequency curve, normalized to 0-100.

        Trapezoid on the log-frequency axis, which is the axis CSF is
        conventionally read on. Normalized against a flat curve at the
        reference sensitivity so the number stays comparable across protocols
        with different frequency ranges.
        """
        if not self.is_usable:
            return None
        f = [math.log10(x) for x in self.spatial_frequency_cpd]
        span = f[-1] - f[0]
        if span <= 0:
            return None
        area = sum(
            0.5 * (self.log_cs[i] + self.log_cs[i + 1]) * (f[i + 1] - f[i])
            for i in range(len(f) - 1)
        )
        ref = self.normalization_reference or DEFAULT_NORMALIZATION_REFERENCE
        return round(area / (ref * span) * 100.0, 1)

    def slope(self) -> Optional[float]:
        """Log contrast sensitivity lost per decade of spatial frequency.

        Least squares over all points rather than endpoint-to-endpoint, so a
        protocol with more than three frequencies uses all of them.
        """
        if not self.is_usable:
            return None
        xs = [math.log10(v) for v in self.spatial_frequency_cpd]
        ys = list(self.log_cs)
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs)
        if denom <= 0:
            return None
        return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom, 3)

    def centroid_cpd(self) -> Optional[float]:
        """Where the sensitivity mass sits on the frequency axis."""
        if not self.is_usable:
            return None
        total = sum(self.log_cs)
        if total <= 0:
            return None
        return round(
            sum(v * f for v, f in zip(self.log_cs, self.spatial_frequency_cpd))
            / total,
            2,
        )

    # ------------------------------------------------------------------ #

    def descriptors(self) -> Dict[str, Optional[float]]:
        return {
            "csf_auc": self.auc(),
            "csf_slope": self.slope(),
            "csf_centroid_cpd": self.centroid_cpd(),
        }

    def as_record(self) -> Dict[str, Any]:
        """The stored schema. Protocol travels with the numbers."""
        return {
            "device": self.device,
            "test_protocol": self.test_protocol,
            "spatial_frequency_cpd": list(self.spatial_frequency_cpd),
            "raw_sensitivity": list(self.raw_sensitivity or []),
            "log_cs": list(self.log_cs or []),
            "normalization_reference": self.normalization_reference,
            "frequencies_assumed": self.frequencies_assumed,
            **self.descriptors(),
        }


def from_triplet(
    low: float,
    mid: float,
    high: float,
    frequencies: Optional[Sequence[float]] = None,
    device: str = "unspecified",
    test_protocol: str = "unspecified",
    scale: str = "index_0_100",
) -> CsfMeasurement:
    """Build a measurement from the three-value form the UI collects.

    ``scale`` says what the three numbers mean. ``index_0_100`` is the legacy
    0-100 index the interface has always used; ``log_cs`` is what an instrument
    reports directly. Recording which one was used is the difference between a
    number that can be compared across clinics and one that cannot.

    When ``frequencies`` is omitted the nominal 1.5 / 6 / 18 cpd are used and
    the measurement is flagged ``frequencies_assumed`` -- so the assumption is
    visible in the record rather than buried in the engine.
    """
    assumed = frequencies is None
    freqs = list(frequencies or ASSUMED_FREQUENCIES_CPD)

    if scale == "log_cs":
        log_cs = [float(low), float(mid), float(high)]
    else:
        # A 0-100 index maps onto the reference log range.
        log_cs = [
            float(v) / 100.0 * DEFAULT_NORMALIZATION_REFERENCE
            for v in (low, mid, high)
        ]

    return CsfMeasurement(
        spatial_frequency_cpd=freqs,
        log_cs=log_cs,
        device=device,
        test_protocol=test_protocol,
        frequencies_assumed=assumed,
    )
