# -*- coding: utf-8 -*-
"""Power reference-layout templates (knowhow: ChengminLi loop-aware layout,
FulongLi real-copper loop screening, Konnect reference-circuit templates).

Datasheet-style placement for the power chain: loop-area-minimized
regulator+capacitor clusters. Each template returns relative offsets from
the regulator anchor so placement code computes clusters instead of fixed
grid coordinates.
"""
from __future__ import annotations

# offsets: (ref, dx, dy) in mm relative to the regulator anchor.
# Loop-area minimization: C_in hugs VIN side, C_out hugs VOUT side, all
# within ~2.5mm of the IC body (datasheet layout guidance).

LDO_SOT23_AP2112K = {
    # AP2112K SOT-23-5: pin1/3=VIN(left), pin5=VOUT(right), pin2=GND(bottom)
    "caps": [
        ("C_in", -2.54, 0.0),    # flanking VIN side
        ("C_out", 2.54, 0.0),    # flanking VOUT side
    ],
    "max_loop_area_mm2": 12.0,   # C_in->IC->C_out triangle budget
}

# N6 internal SMPS: inductor hugs the VLXSMPS ball row; VDDCORE decoupling
# clusters around the load-side balls
SMPS_VLX_INDUCTOR = {
    "inductor": (0.0, -9.0),     # just outside the BGA VLX ball row
    "vddcore_caps": [(2.0, -11.0), (-2.0, -11.0)],
    "max_loop_area_mm2": 20.0,
}

# Buck (TLV62569-class, SOT-23-5/6): shortest SW->inductor->VOUT loop
BUCK_SOT23 = {
    "inductor": (2.8, 0.0),
    "c_in": (-2.54, 1.27),
    "c_out": (5.2, 0.0),
    "max_loop_area_mm2": 15.0,
}


def ldo_cluster(anchor: tuple[float, float],
                c_in_ref: str = "C_in", c_out_ref: str = "C_out") -> dict:
    """Placement entries for an LDO + caps around an anchor."""
    x, y = anchor
    return {
        c_in_ref: (x + LDO_SOT23_AP2112K["caps"][0][1],
                   y + LDO_SOT23_AP2112K["caps"][0][2]),
        c_out_ref: (x + LDO_SOT23_AP2112K["caps"][1][1],
                    y + LDO_SOT23_AP2112K["caps"][1][2]),
    }


def loop_area_mm2(a: tuple, b: tuple, c: tuple) -> float:
    """Triangle area of the C_in -> IC -> C_out current loop."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    return abs(ax * (by - cy) + bx * (cy - ay) + cx * (ay - by)) / 2.0


def loop_ok(a, b, c, budget_mm2: float = 12.0) -> bool:
    return loop_area_mm2(a, b, c) <= budget_mm2
