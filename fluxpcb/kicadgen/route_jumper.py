# -*- coding: utf-8 -*-
"""Deterministic 0R jumper closure — the ratsnest=0 guarantee.

Absorbs deosgracius/spicestudio-ai hop-over jumpers, with the correct
island-split semantics:

  For every open pair, one island keeps the original NET; the OTHER
  island's copper (pads) is RENAMED to NET#Jn; a 0R 0603 jumper (loaded
  from a real footprint file via FootprintLoad) bridges
  pad1:NET <-> pad2:NET#Jn. The ratsnest closes because each net is a
  single island afterwards; the 0R closes them electrically.

Runs inside KiCad Python:  route_jumper.py PCB [PRETTYDIR]
Prints one JSON line: {jumpers, detail[], remaining}
"""
import json
import sys
from pathlib import Path as _P

import pcbnew

PCB = sys.argv[1]
PRETTY = sys.argv[2] if len(sys.argv) > 2 else ""
mm = 1000000
board = pcbnew.LoadBoard(PCB)

_FP_NAME = "R_0603_J"
_FP_TEXT = '''(footprint "flux:R_0603_J" (version 20221018) (generator fluxpcb)
  (layer "F.Cu") (attr smd)
  (descr "0R jumper 0603 (fluxpcb route_jumper)")
  (property "Reference" "JMP**" (at 0 -1.5 0) (layer "F.SilkS")
    (effects (font (size 0.8 0.8) (thickness 0.12))))
  (property "Value" "0R" (at 0 1.5 0) (layer "F.Fab")
    (effects (font (size 0.8 0.8) (thickness 0.12))))
  (fp_line (start -1.2 -0.8) (end 1.2 -0.8) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start 1.2 -0.8) (end 1.2 0.8) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start 1.2 0.8) (end -1.2 0.8) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start -1.2 0.8) (end -1.2 -0.8) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (pad "1" smd rect (at -0.75 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at 0.75 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask"))
)
'''

pretty = _P(PRETTY) if PRETTY else _P(PCB).parent / "jumper_pretty"
pretty.mkdir(parents=True, exist_ok=True)
fp_file = pretty / f"{_FP_NAME}.kicad_mod"
if not fp_file.exists():
    fp_file.write_text(_FP_TEXT, encoding="utf-8")

_bb = board.GetBoardEdgesBoundingBox()
EDGE = (_bb.GetX() / mm, _bb.GetY() / mm,
        (_bb.GetX() + _bb.GetWidth()) / mm,
        (_bb.GetY() + _bb.GetHeight()) / mm)


def pt_seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def is_via(it):
    return it.Type() == pcbnew.PCB_VIA_T


def track_w(it):
    try:
        return it.GetWidth(pcbnew.F_Cu) / mm / 2
    except TypeError:
        try:
            return it.GetWidth() / mm / 2
        except TypeError:
            return 0.1


# ---------------------------------------------------------------- audit
def open_islands():
    """[(name, nc, [island_pads...] >=2)] via position-keyed union-find."""
    board.BuildConnectivity()
    conn = board.GetConnectivity()

    def anchors(item):
        t = type(item).__name__
        if t in ("PAD", "PCB_VIA"):
            q = item.GetPosition()
            return [(q.x, q.y)]
        if t == "PCB_TRACK":
            s, e = item.GetStart(), item.GetEnd()
            return [(s.x, s.y), (e.x, e.y)]
        return []

    class UF:
        def __init__(self):
            self.p = {}

        def find(self, x):
            self.p.setdefault(x, x)
            while self.p[x] != x:
                self.p[x] = self.p[self.p[x]]
                x = self.p[x]
            return x

        def union(self, a, b):
            ra, rb = self.find(a), self.find(b)
            if ra != rb:
                self.p[ra] = rb

    out = []
    pads_by_net = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if nc:
                pads_by_net.setdefault(nc, []).append(pad)
    for nc, pads in pads_by_net.items():
        if nc == 0 or len(pads) < 2:
            continue
        uf = UF()
        keys = [anchors(p)[0] for p in pads]
        for k in keys:
            uf.find(k)
        for pad, k in zip(pads, keys):
            try:
                for nb in conn.GetConnectedItems(pad):
                    if nb.GetNetCode() != nc:
                        continue
                    for a in anchors(nb):
                        uf.union(k, a)
            except (AttributeError, TypeError):
                pass
        islands = {}
        for pad, k in zip(pads, keys):
            islands.setdefault(uf.find(k), []).append(pad)
        if len(islands) < 2:
            continue
        out.append((pads[0].GetNetname(), nc, list(islands.values())))
    return out


# ---------------------------------------------------------------- spot
def spot_ok(x, y):
    """0603 jumper window: both pad centers clear of any F.Cu copper."""
    for it in board.GetTracks():
        try:
            if is_via(it):
                q = it.GetPosition()
                ax, ay, bx, by = q.x / mm, q.y / mm, q.x / mm, q.y / mm
                r = it.GetWidth(pcbnew.F_Cu) / mm / 2
            elif it.GetLayer() == pcbnew.F_Cu:
                s, e = it.GetStart(), it.GetEnd()
                ax, ay, bx, by = s.x / mm, s.y / mm, e.x / mm, e.y / mm
                r = track_w(it)
            else:
                continue
        except AttributeError:
            continue
        for dx in (-0.75, 0.75):
            if pt_seg_dist(x + dx, y, ax, ay, bx, by) < r + 0.55:
                return False
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                if not pad.IsOnLayer(pcbnew.F_Cu):
                    continue
                q = pad.GetPosition()
                sz = pad.GetSize()
                r = max(sz.x, sz.y) / mm / 2
            except AttributeError:
                continue
            for dx in (-0.75, 0.75):
                if pt_seg_dist(x + dx, y, q.x / mm, q.y / mm,
                               q.x / mm, q.y / mm) < r + 0.55:
                    return False
    return True


def find_spot(x, y):
    import math
    for i in range(1, 14):
        r = 0.9 * i
        for k in range(16):
            a = 2 * math.pi * k / 16
            x2, y2 = x + r * math.cos(a), y + r * math.sin(a)
            if not (EDGE[0] + 2.0 < x2 < EDGE[2] - 2.0 and
                    EDGE[1] + 2.0 < y2 < EDGE[3] - 2.0):
                continue
            if spot_ok(x2, y2):
                return (x2, y2)
    return None


# ---------------------------------------------------------------- close
def close(idx, name, nc, isl_keep, isl_rename):
    net1 = board.FindNet(name)
    if net1 is None:
        return None
    split = f"{name}#J{idx}"
    board.Add(pcbnew.NETINFO_ITEM(board, split))
    new_net = board.FindNet(split)
    if new_net is None:
        return None
    renamed = 0
    for it in isl_rename:
        try:
            it.SetNet(new_net)
            renamed += 1
        except AttributeError:
            continue
    rollback = lambda: [it.SetNet(net1) for it in isl_rename
                        if hasattr(it, "SetNet")]

    cx0, cy0 = (EDGE[0] + EDGE[2]) / 2, (EDGE[1] + EDGE[3]) / 2

    def anchor_of(isl):
        best, bd = None, 1e18
        for it in isl:
            q = it.GetPosition()
            d = abs(q.x / mm - cx0) + abs(q.y / mm - cy0)
            if d < bd:
                best, bd = it, d
        return best

    # jumper at the BIDIRECTIONAL-FLOOD MEETING POINT (route_diag model):
    # both floods reached that cell through the SAME free-grid semantics
    # the session A* uses — so both halves anchor->pad1 / pad2->anchor are
    # provably routable. A midpoint guess landed in packed field where
    # neither half could route.
    import os as _os
    import sys as _sys
    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    try:
        import route_diag as _rd
        _dm = _rd.Diag()
    except Exception:
        _dm = None
    spot = None
    if _dm is not None:
        try:
            c1 = _rd.anchor_cell(_dm, q1.x / mm, q1.y / mm, 0, nc)
            c2 = _rd.anchor_cell(_dm, q2.x / mm, q2.y / mm, 0, nc)
            if c1 and c2:
                ga, _ = _rd.flood(_dm, nc, c1)
                gb, _ = _rd.flood(_dm, nc, c2)
                small, big = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
                meet = None
                for cell, g in small.items():
                    g2 = big.get(cell)
                    if g2 is not None and (meet is None or g + g2 < meet[0]):
                        meet = (g + g2, cell)
                if meet:
                    _, (li, gx, gy) = meet
                    spot = find_spot((gx + 0.5) * _rd.GRID,
                                     (gy + 0.5) * _rd.GRID)
        except Exception:
            spot = None
    if spot is None:
        a1, a2 = anchor_of(isl_keep), anchor_of(isl_rename)
        if a1 is None or a2 is None:
            rollback()
            return None
        q1, q2 = a1.GetPosition(), a2.GetPosition()
        spot = find_spot((q1.x / mm + q2.x / mm) / 2,
                         (q1.y / mm + q2.y / mm) / 2)
    if spot is None:
        rollback()
        return None
    fp = pcbnew.FootprintLoad(str(pretty), _FP_NAME)
    if fp is None:
        rollback()
        return None
    fp.SetReference(f"JMP{idx}")
    fp.SetValue("0R")
    fp.SetPosition(pcbnew.VECTOR2I(int(spot[0] * mm), int(spot[1] * mm)))
    pads = list(fp.Pads())
    pads[0].SetNet(net1)
    pads[1].SetNet(new_net)
    board.Add(fp)
    return {"ref": f"JMP{idx}", "net": name, "split": split,
            "renamed": renamed,
            "at": [round(spot[0], 2), round(spot[1], 2)]}


def main():
    """SINGLE pass over the initial opens — a re-audit loop cascades
    (jumper pads are unconnected, so each split net re-opens and gets
    jumpered again: 21 jumpers on a 3-open board). The session routes
    the halves afterwards; only re-run this script deliberately."""
    results = []
    idx = 0
    pairs = open_islands()
    board.BuildConnectivity()
    before = board.GetConnectivity().GetUnconnectedCount(True)
    for (name, nc, isl) in pairs:
        if len(isl) < 2:
            continue
        idx += 1
        isl.sort(key=len)
        info = close(idx, name, nc, isl[-1], isl[0])
        if info:
            results.append(info)
    board.BuildConnectivity()
    remaining = board.GetConnectivity().GetUnconnectedCount(True)
    board.Save(PCB)
    print(json.dumps({"jumpers": len(results), "before": before,
                      "detail": results, "remaining": remaining},
                     ensure_ascii=False))


main()
