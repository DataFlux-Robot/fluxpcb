"""IPC-2141 closed-form impedance solver (pure Python, no external deps).

Reference: IPC-2141A "Design Guide for High-Speed Controlled Impedance
Circuit Boards". Accuracy vs 2D field solver is typically within 5% for
w/h in [0.1, 2.0].
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _eff_er_microstrip(w: float, h: float, t: float, er: float) -> tuple[float, float]:
    """Effective dielectric constant of microstrip (Hammerstad simplified, with
    IPC-2141 finite-thickness width correction)."""
    u = w / h
    if t > 0 and u > 0:
        # IPC-2141 3.3.1 effective-width correction
        du = (t / (2 * math.pi * h)) * (
            1 + math.log(4 * math.e * u / ((t / h) ** 2 + 1.0)))
        u = u + du
    if u <= 1:
        corr = 1 / math.sqrt(1 + 12 / u) + 0.04 * (1 - u) ** 2
    else:
        corr = 1 / math.sqrt(1 + 12 / u)
    ee = (er + 1) / 2 + (er - 1) / 2 * corr
    return ee, u


def microstrip_z0(w: float, h: float, er: float, t: float = 0.035) -> float:
    """Characteristic impedance of surface microstrip, mm units, ohms."""
    ee, u = _eff_er_microstrip(w, h, t, er)
    if u < 1:
        z = 60 / math.sqrt(ee) * math.log(8 / u + u / 4)
    else:
        z = 120 * math.pi / (math.sqrt(ee) * (u + 1.393 + 0.667 * math.log(u + 1.444)))
    return z


def stripline_z0(w: float, h: float, er: float, t: float = 0.035) -> float:
    """Symmetric (centered) stripline, mm units, ohms. h = distance between the two planes."""
    b = h  # plane-to-plane spacing; trace centered
    u = w / (b - t)
    z = 60 / math.sqrt(er) * math.log(4 * b / (0.67 * math.pi * (0.8 * w + t)))
    return z


def edge_coupled_microstrip_zdiff(w: float, s: float, h: float, er: float,
                                  t: float = 0.035) -> tuple[float, float]:
    """Edge-coupled differential microstrip: returns (Zodd, Zdiff), mm units."""
    z0 = microstrip_z0(w, h, er, t)
    # Bahl's closed form coupling correction
    k = math.exp(-0.96 * s / h) if h > 0 else 0
    zdiff = 2 * z0 * (1 - 0.48 * k)
    zodd = zdiff / 2
    return zodd, zdiff


def edge_coupled_stripline_zdiff(w: float, s: float, h: float, er: float,
                                 t: float = 0.035) -> tuple[float, float]:
    z0 = stripline_z0(w, h, er, t)
    k = math.exp(-1.35 * s / h + 0.11) if h > 0 else 0
    zdiff = 2 * z0 * (1 - 0.37 * math.exp(-2.9 * s / h)) if h > 0 else 2 * z0
    zodd = zdiff / 2
    return zodd, zdiff


@dataclass
class ImpedanceTarget:
    net_class: str
    style: str            # microstrip | stripline
    z_target: float       # single-ended ohm (or Zdiff if differential)
    tolerance_pct: float = 10.0
    differential: bool = False


@dataclass
class ImpedanceResult:
    net_class: str
    computed_z: float
    z_target: float
    deviation_pct: float
    ok: bool
    params: dict

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def solve_for_width(target: "ImpedanceTarget", h: float, er: float, t: float = 0.035,
                    s: float | None = None) -> tuple[float, ImpedanceResult]:
    """Bisection search for the trace width (and gap if differential) hitting the target.

    Returns (width_mm, ImpedanceResult). For differential targets, gap s is fixed
    and width is solved so that Zdiff matches.
    """
    def z_of(w: float) -> float:
        if target.style == "microstrip":
            if target.differential:
                return edge_coupled_microstrip_zdiff(w, s or w, h, er, t)[1]
            return microstrip_z0(w, h, er, t)
        if target.differential:
            return edge_coupled_stripline_zdiff(w, s or w, h, er, t)[1]
        return stripline_z0(w, h, er, t)

    lo, hi = 0.02, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if z_of(mid) > target.z_target:
            lo = mid
        else:
            hi = mid
    w = (lo + hi) / 2
    z = z_of(w)
    dev = (z - target.z_target) / target.z_target * 100
    res = ImpedanceResult(
        net_class=target.net_class, computed_z=round(z, 2), z_target=target.z_target,
        deviation_pct=round(dev, 3), ok=abs(dev) <= target.tolerance_pct,
        params={"width_mm": round(w, 4), "s_mm": s, "h_mm": h, "er": er, "t_mm": t,
                "style": target.style, "differential": target.differential})
    return w, res
