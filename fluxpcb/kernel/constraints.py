# -*- coding: utf-8 -*-
"""Board State Kernel — constraints.py: downstream→upstream edges (C4).

The binding-time defect (points 3/6): pin assignment and placement
committed their decisions with NO downstream (routing) feedback —
35 of fc's opens were pin-assignment crossing conflicts manufactured
at allocation time, unfixable downstream. Constraint objects make the
upstream binding stages hear the downstream BEFORE they bind:

    routing failure  ->  EscapeConstraint(toward-outside direction)
                      ->  pin reassignment prefers balls on the escape
                          side (regenerate board+schema consistently)

These are data, not patches: the SAME constraint object feeds the
decision layer's candidates AND the regeneration input.
"""
from __future__ import annotations

import json
from pathlib import Path


def escape_preferences(pairs: list, taxonomy: dict) -> dict:
    """For every escape-crossing net, which direction its BGA anchor
    must escape toward (the outside anchor's bearing).

    pairs: net|x1|y1|l1|x2|y2|l2 lines (kernel conn_audit format)
    returns {net: (dx, dy)} unit bearing from the BGA anchor to the
    outside anchor — the pin-reassignment cost direction.
    """
    from .causal import bga_boxes_from_pairs, _parse_pairs, _in
    parsed = _parse_pairs(pairs)
    boxes = bga_boxes_from_pairs(parsed)
    out = {}
    want = set(taxonomy.get("escape-crossing", []))
    for net, a, b in parsed:
        if net not in want:
            continue
        a_in = any(_in(bx, a) for bx in boxes)
        inside, outside = (a, b) if a_in else (b, a)
        dx, dy = outside[0] - inside[0], outside[1] - inside[1]
        n = (dx * dx + dy * dy) ** 0.5 or 1.0
        out[net] = (dx / n, dy / n)
    return out


def write_pin_override(project_dir: Path, overrides: dict) -> Path:
    """FC_PIN_OVERRIDE.json = {signal_name: ball_position} consumed by
    fcfull.mcu_ball_nets — the regeneration contract (board + schematic
    both read it, so the two never diverge)."""
    p = project_dir / "FC_PIN_OVERRIDE.json"
    p.write_text(json.dumps(overrides, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p


def reassign_proposal(ball_nets: dict, prefs: dict, pins_db: dict,
                      mcu_center, limit: int = 12) -> dict:
    """Propose ball swaps for escape-crossing nets.

    ball_nets: {signal: {"ball", ...}} current assignment (mcu_db form)
    prefs:     {net/signal: (dx,dy)} escape bearing
    pins_db:   {ball: {"signals": [...]}} AF multiplexing table
    mcu_center: (x_mm, y_mm)

    Score = alignment of (ball - center) bearing with the escape
    bearing; a candidate ball must carry the signal in its AF family
    and be currently occupied by an AF-swappable signal. Returns the
    override dict {signal: new_ball} (empty when nothing helps).
    """
    import math
    # reverse index: signal -> [balls carrying it]
    rev = {}
    for ball, info in pins_db.items():
        for sig in info.get("signals", []):
            rev.setdefault(sig, []).append(ball)

    def ball_xy(ball):
        # mcu_db ball keys are grid coords 'x,y' (strings) on h743
        try:
            xs, ys = str(ball).split(",")
            return float(xs), float(ys)
        except ValueError:
            return None

    owner_of = {}
    for sig, d in ball_nets.items():
        owner_of[d["ball"]] = sig

    out = {}
    for sig, (dx, dy) in list(prefs.items()):
        if sig not in rev or len(out) >= limit:
            continue
        cur = ball_nets.get(sig)
        if not cur:
            continue
        cur_xy = ball_xy(cur["ball"])
        if cur_xy is None:
            continue
        cx, cy = mcu_center
        best = None
        for ball in rev[sig]:
            xy = ball_xy(ball)
            if xy is None or ball == cur["ball"]:
                continue
            bx, by = xy[0] - cx, xy[1] - cy
            n = (bx * bx + by * by) ** 0.5 or 1.0
            align = (bx / n) * dx + (by / n) * dy   # cos(angle)
            # current ball's alignment for comparison
            cxx, cyy = cur_xy[0] - cx, cur_xy[1] - cy
            n2 = (cxx * cxx + cyy * cyy) ** 0.5 or 1.0
            cur_align = (cxx / n2) * dx + (cyy / n2) * dy
            if align > cur_align + 0.2 and (best is None
                                            or align > best[1]):
                best = (ball, align)
        if best:
            out[sig] = best[0]
    return out
