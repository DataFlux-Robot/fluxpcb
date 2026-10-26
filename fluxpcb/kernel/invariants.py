# -*- coding: utf-8 -*-
"""Board State Kernel — invariants.py: the enforced lessons.

Facet 5 of the kernel: a generic lesson's only true home. Each
invariant kills a FAILURE CLASS (not a bug):

  I1 no_crossnet_overlap — engines once emitted overlapping stubs
     (fc: 59 shorts, 85% grid-step micro-stubs). Class: any engine's
     internal model diverging from world geometry.
  I2 clearance_floor     — spacing must stay above the fabrication
     floor (JLC inner 1oz: 0.09mm) after waivers. Class: near-miss
     geometry accumulating invisibly.
  I3 opens_no_regress    — a transaction must not destroy more
     connectivity than it creates (cleanup once removed load-bearing
     copper: 3->19 opens). Class: repair steps that damage.
  I4 bound_measurements  — enforced structurally by state.KernelQuery
     (numbers cannot exist without a fingerprint). Class: stale/lying
     counters (the severity-calibration illusion family).

New invariants get added HERE as incidents generalize them.
"""
from __future__ import annotations

FAB_FLOOR_MM = 0.09          # JLC 1oz inner-layer spacing capability;
                             # 2oz would be 0.15 — set per project later

INVARIANT_LEVELS = ("light", "full")


def check_level(level: str, pre: dict, post: dict,
                tolerance: int = 5) -> list:
    """Return the list of violated invariant descriptions ([] = clean).

    I1 (cross-net overlap) and I2 (clearance floor) are checked via the
    DRC counters the measurement already collected (shorting count is
    the board-level overlap proxy; full-level runs one DRC). A full
    geometric sweep lives in commit_gate for per-commit use; here we
    need the cheap, always-correct board-level verdict.
    """
    v = []
    # I3: connectivity may not regress beyond tolerance
    if pre.get("opens") is not None and post.get("opens") is not None:
        if post["opens"] > (pre["opens"] + tolerance):
            v.append(f"I3 opens {pre['opens']}->{post['opens']}")
    if level != "full":
        return v
    # I1 proxy: shorts must not increase
    if pre.get("shorts") is not None and post.get("shorts") is not None:
        if post["shorts"] > pre["shorts"]:
            v.append(f"I1 shorts {pre['shorts']}->{post['shorts']}")
    # I2 proxy: clearance count may not increase (waivers applied
    # downstream of DRC counting; floor enforcement is the DRC rule
    # itself, calibrated to the fab floor)
    if (pre.get("clearance") is not None
            and post.get("clearance") is not None
            and post["clearance"] > pre["clearance"]):
        v.append(f"I2 clearance {pre['clearance']}->{post['clearance']}")
    return v
