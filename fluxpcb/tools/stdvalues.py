"""E-series (E12/E24/E96) preferred values — deterministic passive selection.

Computational resistor/capacitor values (divider legs, pull-ups, decoupling)
are snapped to market-available standard values before entering the BOM.
"""
from __future__ import annotations

# E-series significant-figure tables
_E12 = list(range(10, 100, 10))                      # 10..90
_E24 = [10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30,
        33, 36, 39, 43, 47, 51, 56, 62, 68, 75, 82, 91]
_E96 = [100, 102, 105, 107, 110, 113, 115, 118, 121, 124, 127, 130,
        133, 137, 140, 143, 147, 150, 154, 158, 162, 165, 169, 174,
        178, 182, 187, 191, 196, 200, 205, 210, 215, 221, 226, 232,
        237, 243, 249, 255, 261, 267, 274, 280, 287, 294, 301, 309,
        316, 324, 332, 340, 348, 357, 365, 374, 383, 392, 402, 412,
        422, 432, 442, 453, 464, 475, 487, 499, 511, 523, 536, 549,
        562, 576, 590, 604, 619, 634, 649, 665, 681, 698, 715, 732,
        750, 768, 787, 806, 825, 845, 866, 887, 909, 931, 953, 976]

SERIES = {"E12": _E12, "E24": _E24, "E96": _E96}


def snap(value: float, series: str = "E96") -> float:
    """Snap an arbitrary positive value to the nearest preferred value."""
    if value <= 0:
        raise ValueError("value must be positive")
    table = SERIES[series]
    mant = value
    decade = 0
    while mant >= 1000:      # normalize to [10, 1000)
        mant /= 10.0
        decade += 1
    while mant < 10:
        mant *= 10.0
        decade -= 1
    # nearest candidate across decade boundary
    best, best_err = None, float("inf")
    for d in (decade - 1, decade, decade + 1):
        for sig in table:
            cand = sig * (10.0 ** d)
            err = abs(cand - value) / value
            if err < best_err:
                best, best_err = cand, err
    return best


def fmt_res(ohms: float) -> str:
    """Format a resistance value the way BOMs/datasheets do: 10k, 5.1k, 1k, 100R."""
    if ohms >= 1e6:
        v = ohms / 1e6
        return _trim(v) + "M"
    if ohms >= 1e3:
        v = ohms / 1e3
        return _trim(v) + "k"
    return _trim(ohms) + "R"


def fmt_cap(farads: float) -> str:
    """Format a capacitance value: 100nF, 10uF, 22pF."""
    if farads >= 1e-6:
        return _trim(farads / 1e-6) + "uF"
    if farads >= 1e-9:
        return _trim(farads / 1e-9) + "nF"
    return _trim(farads / 1e-12) + "pF"


def _trim(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def divider(target_ratio: float, series: str = "E96",
            upper_pref: float | None = None) -> tuple[float, float]:
    """Pick a divider pair (top, bottom) whose ratio is closest to target.

    upper_pref: if given, the top leg is snapped to this value first
    (e.g. 10k pull-up) and only the bottom leg is searched.
    """
    best, best_err = None, float("inf")
    if upper_pref is not None:
        top = snap(upper_pref, series)
        bottom = snap(top / target_ratio, series)
        return top, bottom
    for top_sig in SERIES[series]:
        for bot_sig in SERIES[series]:
            for d in (0, 1, 2):
                top = top_sig * (10.0 ** d)
                bot = bot_sig * (10.0 ** d)
                ratio = top / bot
                err = abs(ratio - target_ratio) / target_ratio
                if err < best_err:
                    best, best_err = (top, bot), err
    return best
