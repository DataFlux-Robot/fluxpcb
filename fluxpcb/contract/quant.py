"""quant — nanometre-exact quantities and indeterminate comparisons.

KiCad's internal unit is 1 nm, chosen so both mil and mm are exact:
0.1524 mm = 6 mil = 152 400 nm.  Any channel that narrows below the
source resolution (integer micrometres, for instance) loses information;
the loss is only harmless if the receiver refuses to decide inside the
band it cannot resolve.

``compare_min`` implements that refusal.  It returns INDETERMINATE when
the measured value and the limit are closer than the channel's resolution,
instead of letting a rounding policy silently become a design rule.
"""
from __future__ import annotations

from enum import Enum

NM_PER_UM = 1000.0
NM_PER_MM = 1_000_000.0


class Cmp(str, Enum):
    PASS = 'pass'
    FAIL = 'fail'
    INDETERMINATE = 'indeterminate'


def mm_to_nm(value) -> float:
    """Millimetres -> nanometres (float, exact for the usual mm literals)."""
    return float(value) * NM_PER_MM


def parse_um(value) -> float:
    """A DSN micrometre literal (int or float) -> nanometres."""
    return float(value) * NM_PER_UM


def nm_to_um_str(nm: float) -> str:
    """Nanometres -> the shortest micrometre literal (``%.6g``).

    152 400 nm -> '152.4', never '152'.  KiCad itself writes DSN values the
    same way; an integer-µm channel is our adapter's choice, not the format's.
    """
    return f'{float(nm) / NM_PER_UM:.6g}'


def um_str_to_nm(text: str) -> float:
    return float(text) * NM_PER_UM


def compare_min(measured_nm: float, limit_nm: float,
                resolution_nm: float = 0.0) -> Cmp:
    """Compare a measured quantity against a minimum limit.

    Args:
        measured_nm: the measured value in nanometres.
        limit_nm: the required minimum in nanometres.
        resolution_nm: the measurement channel's granularity.  When the
            gap is not larger than half a resolution step, the sign of the
            comparison is not determined by the data; report INDETERMINATE.

    Returns:
        Cmp.PASS if measured is at or above the limit by more than the band,
        Cmp.FAIL if it is below by more than the band, else INDETERMINATE.
    """
    band = max(0.0, float(resolution_nm)) / 2.0
    gap = float(measured_nm) - float(limit_nm)
    # A zero band means the channel is exact: equality is a pass.
    if band > 0.0 and abs(gap) <= band:
        return Cmp.INDETERMINATE
    return Cmp.PASS if gap >= 0.0 else Cmp.FAIL
