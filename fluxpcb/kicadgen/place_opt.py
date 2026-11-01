# -*- coding: utf-8 -*-
"""place_opt — routability-driven placement moves (stream ①).

The residual-opens root cause (four-source evidence): routing problems
born at placement time. UCSD margin-max: placement-only changes cut DRV
~80%. This module makes placement a MOVER in the closed loop:

- margin_ok()    — "check space before moving" (DaCapo7 gap_nudge):
  the target position's margin is a PRECONDITION, not an afterthought
  (kabidey: fix placement, never star-links or board-shrink)
- rank_moves()   — the attribution-driven candidate bias, the
  lightweight stand-in for elcruzo's GNN "which component to move and
  where": the residual net's map names its blockers; we bias toward
  moving the blocker-most component AWAY from the failed corridor and
  the smallest move that helps (pcbfork: smallest |ΔPCB| that improves)

Honest scope: bbox+gap margin (courtyard shapes when available), a
geometric scorer not a learned one — the strict-improvement gate is
the judge, the scorer only ranks.

Usage:
  place_opt.py PCB NET            -> ranked candidates (dry-run)
  place_opt.py PCB NET --apply DX DY REF -> move + rip+FR the net
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pcbnew

MM = 1000000
EDGE_GAP = 0.5          # mm to board edge
FP_GAP = 0.2            # mm between footprints (courtyard-ish floor)


# ------------------------------------------------------- margin checking
def _fp_shape(fp):
    """(cx, cy, hw, hh) — courtyard when present and non-degenerate,
    else bbox, else the footprint POSITION as a point (empty fps return
    a zero box at the origin — treat as anchored at their position)."""
    q = fp.GetPosition()
    px, py = q.x / MM, q.y / MM
    for getter in (lambda: fp.GetCourtyard(pcbnew.F_CrtYd).BBox(),
                   lambda: fp.GetBoundingBox()):
        try:
            bb = getter()
            hw = bb.GetWidth() / 2 / MM
            hh = bb.GetHeight() / 2 / MM
            if hw > 1e-6 or hh > 1e-6:
                cx = (bb.GetX() + bb.GetWidth() / 2) / MM
                cy = (bb.GetY() + bb.GetHeight() / 2) / MM
                return cx, cy, hw, hh
        except Exception:
            continue    # ok-silent: 单焊盘探测失败回落下一形状
    return px, py, 0.0, 0.0


def margin_ok(board, ref, dx, dy):
    """DaCapo7 precondition: is there ROOM at the moved position?
    Returns {"ok", "margin_mm", "reason"} — the largest uniform gap
    available at the target (negative = overlap depth)."""
    fp = None
    for f in board.GetFootprints():
        if f.GetReference() == ref:
            fp = f
            break
    if fp is None:
        return {"ok": False, "margin_mm": 0.0, "reason": f"{ref} missing"}
    cx, cy, hw, hh = _fp_shape(fp)
    cx2, cy2 = cx + dx, cy + dy
    # board edge
    bb = board.GetBoardEdgesBoundingBox()
    x0, y0 = bb.GetX() / MM, bb.GetY() / MM
    x1 = x0 + bb.GetWidth() / MM
    y1 = y0 + bb.GetHeight() / MM
    edge_margin = min(cx2 - hw - x0, cy2 - hh - y0,
                      x1 - (cx2 + hw), y1 - (cy2 + hh))
    if edge_margin < EDGE_GAP:
        return {"ok": False, "margin_mm": round(edge_margin, 3),
                "reason": "board-edge"}
    worst = edge_margin
    for f in board.GetFootprints():
        if f.GetReference() == ref:
            continue
        ox, oy, ohw, ohh = _fp_shape(f)
        # axis gap between the two boxes
        gx = abs(cx2 - ox) - (hw + ohw)
        gy = abs(cy2 - oy) - (hh + ohh)
        gap = max(gx, gy) if (gx > 0 or gy > 0) else max(gx, gy)
        worst = min(worst, gap)
    if worst < FP_GAP:
        return {"ok": False, "margin_mm": round(worst, 3),
                "reason": "footprint-overlap"}
    return {"ok": True, "margin_mm": round(worst, 3), "reason": "clear"}


# ------------------------------------------------------- candidate bias
def rank_moves(board, plan, limit=8):
    """Attribution-driven candidate ranking (the GNN-lite bias).

    plan (from geomir): {"islands", "rejects", "blockers"}. Candidates:
      * blocker-owner footprints moved AWAY from the corridor (the
        corridor runs between the island anchors; the move direction
        pushes the blocker out of the a-b band)
      * island-owner footprints nudged along the corridor direction
    Filtered by margin_ok, ranked by (moved-blockers desc, |move| asc).
    """
    isl = plan.get("islands") or []
    rejects = plan.get("rejects") or []
    if len(isl) < 2:
        return []
    a = tuple(isl[0]["anchor"])
    b = tuple(isl[1]["anchor"])
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    def nearest_ref(x, y):
        best, bd = None, 1e9
        for f in board.GetFootprints():
            q = f.GetPosition()
            d = math.hypot(q.x / MM - x, q.y / MM - y)
            if d < bd:
                best, bd = f.GetReference(), d
        return best

    blocker_refs = []
    for r in rejects[:6]:
        bl = r.get("blocked_by") or {}
        at = bl.get("at")
        if at:
            ref = nearest_ref(at[0], at[1])
            if ref and ref not in blocker_refs:
                blocker_refs.append(ref)
    island_refs = []
    for islx in isl[:2]:
        ref = nearest_ref(*islx["anchor"])
        if ref and ref not in island_refs:
            island_refs.append(ref)

    out = []
    seen = set()
    # scorer upgrade: the FREE-SLOT side, not radial guess. The corridor
    # axis is a->b; the blocker must leave the corridor BAND, so the
    # move direction is PERPENDICULAR to the axis — both sides are
    # candidate directions and margin_ok's margin tells which side is
    # the free slot (larger margin = the opening).
    ax_, ay_ = b[0] - a[0], b[1] - a[1]
    Lax = math.hypot(ax_, ay_) or 1.0
    pux, puy = -ay_ / Lax, ax_ / Lax
    dirs = [(pux, puy), (-pux, -puy)]
    for ref in blocker_refs:
        fp = next((f for f in board.GetFootprints()
                   if f.GetReference() == ref), None)
        if fp is None:
            continue
        q = fp.GetPosition()
        vx, vy = q.x / MM - mid[0], q.y / MM - mid[1]
        L = math.hypot(vx, vy) or 1.0
        ux, uy = vx / L, vy / L
        for dist in (1.0, 2.0, 3.0):
            for dux, duy in dirs + [(ux, uy)]:
                dx, dy = round(dux * dist, 2), round(duy * dist, 2)
                key = (ref, dx, dy)
                if key in seen:
                    continue
                seen.add(key)
                m = margin_ok(board, ref, dx, dy)
                if not m["ok"]:
                    continue
                # the free-slot side ranks up: perpendicular moves that
                # clear the band with margin to spare
                band_bonus = 20 if (dux, duy) in dirs[:2] else 0
                out.append({"ref": ref, "dx_mm": dx, "dy_mm": dy,
                            "kind": "blocker-out-of-band",
                            "margin_mm": m["margin_mm"],
                            "score": 100 + band_bonus
                            + int(m["margin_mm"] * 10) - dist * 5})
    # island moves: along the corridor axis (keep the net's anchors
    # shiftable but gentle — smallest moves first)
    dirx, diry = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dirx, diry) or 1.0
    for ref in island_refs:
        for sgn in (1, -1):
            for dist in (0.5, 1.0):
                dx = round(dirx / L * dist * sgn, 2)
                dy = round(diry / L * dist * sgn, 2)
                key = (ref, dx, dy)
                if key in seen:
                    continue
                seen.add(key)
                m = margin_ok(board, ref, dx, dy)
                if not m["ok"]:
                    continue
                out.append({"ref": ref, "dx_mm": dx, "dy_mm": dy,
                            "kind": "island-nudge",
                            "margin_mm": m["margin_mm"],
                            "score": 50 + int(m["margin_mm"] * 10)
                            - dist * 5})
    out.sort(key=lambda c: -c["score"])
    return out[:limit]


# ------------------------------------------------------- CLI
if __name__ == "__main__":
    pcb, net = sys.argv[1], sys.argv[2]
    b = pcbnew.LoadBoard(pcb)
    if len(sys.argv) > 3 and sys.argv[3] == "--apply":
        dx, dy, ref = float(sys.argv[4]), float(sys.argv[5]), sys.argv[6]
        m = margin_ok(b, ref, dx, dy)
        print(json.dumps({"applied": m["ok"], "margin": m,
                          "net": net}))
        if m["ok"]:
            fp = next(f for f in b.GetFootprints()
                      if f.GetReference() == ref)
            fp.Move(pcbnew.VECTOR2I(int(dx * MM), int(dy * MM)))
            nets = {p.GetNetname() for p in fp.Pads() if p.GetNetname()}
            ripped = 0
            for it in list(b.GetTracks()):
                try:
                    if it.GetNetname() in nets:
                        b.Remove(it)
                        ripped += 1
                except AttributeError:
                    continue    # ok-silent: 畸形对象逐个跳过
            b.Save(pcb)
            print(json.dumps({"moved": ref, "ripped": ripped,
                              "nets": sorted(nets)[:8]}))
    else:
        # dry-run: rank candidates from the live board + a fresh plan
        import geomir
        plan = geomir.plan_net(b, net)
        cands = rank_moves(b, plan)
        print(json.dumps({"net": net, "summary": plan.get("summary", ""),
                          "candidates": cands}, ensure_ascii=False))
