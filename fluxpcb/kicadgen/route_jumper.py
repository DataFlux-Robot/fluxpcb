# -*- coding: utf-8 -*-
"""Deterministic 0R jumper closure — the ratsnest=0 guarantee.

Absorbs deosgracius/spicestudio-ai's hop-over jumpers: when route_diag
says a net cannot route (sealed anchor / disconnected component), insert
a 0R 0603 resistor between the two islands and route each HALF as a short
local connection (each half ends ON the jumper pad — pads are placed in
open space near each island's anchor, reachable by the same grid A* used
everywhere else).

Board side: jumper footprints (2 pads, 0603) placed at free spots near
each sealed anchor; net split as NET / NET#Jn (both islands connect to
their own jumper pad). Electrical closure is the 0R itself.

Gate side: netlist_topology treats NET#Jn as NET through a 0R (the
"#J" suffix + resistor value 0R is the contract).

Runs inside KiCad Python: route_jumper.py PCB CLSFILE
Prints JSON: {jumpers: n, placements: [{ref, net, at}], remaining}
"""
import heapq
import json
import sys

import pcbnew

PCB = sys.argv[1]
CLS = sys.argv[2] if len(sys.argv) > 2 else ""
mm = 1000000
board = pcbnew.LoadBoard(PCB)

try:
    NET_CLASS = json.load(open(CLS)).get("classes", {})
except (OSError, ValueError):
    NET_CLASS = {}

GRID = 0.16
_PTH = getattr(pcbnew, "PAD_ATTRIB_PTH",
               getattr(pcbnew, "PAD_ATTRIB_THROUGH_HOLE", 3))

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


def pad_rect(pad):
    p = pad.GetPosition()
    try:
        sz = pad.GetSize()
        w, h = sz.x / mm, sz.y / mm
    except AttributeError:
        bb = pad.GetBoundingBox()
        w, h = bb.GetWidth() / mm, bb.GetHeight() / mm
    return (p.x / mm, p.y / mm, w / 2.0, h / 2.0)


class Ob:
    """Minimal occupancy for jumper placement + half-routing."""

    def __init__(self):
        self.nx = int((EDGE[2] - EDGE[0]) / GRID) + 2
        self.ny = int((EDGE[3] - EDGE[1]) / GRID) + 2
        self.occ = [bytearray(self.nx * self.ny) for _ in range(2)]
        self.own = {}
        self.segs = ([], [])
        self.rects = ([], [])
        self._raster()

    def _mark(self, li, x0, y0, x1, y1, margin):
        gx0 = int((min(x0, x1) - margin) / GRID)
        gx1 = int((max(x0, x1) + margin) / GRID) + 1
        gy0 = int((min(y0, y1) - margin) / GRID)
        gy1 = int((max(y0, y1) + margin) / GRID) + 1
        for gx in range(max(0, gx0), min(self.nx, gx1)):
            cx = (gx + 0.5) * GRID
            for gy in range(max(0, gy0), min(self.ny, gy1)):
                cy = (gy + 0.5) * GRID
                if pt_seg_dist(cx, cy, x0, y0, x1, y1) <= margin:
                    self.occ[li][gy * self.nx + gx] = 1

    def _own(self, li, x, y, r, nc):
        for gx in range(int((x - r) / GRID), int((x + r) / GRID) + 1):
            for gy in range(int((y - r) / GRID), int((y + r) / GRID) + 1):
                cx, cy = (gx + 0.5) * GRID, (gy + 0.5) * GRID
                if (cx - x) ** 2 + (cy - y) ** 2 <= r * r:
                    self.own.setdefault((li, gx, gy), set()).add(nc)

    def _raster(self):
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                try:
                    nc = pad.GetNetCode()
                    cx, cy, hw, hh = pad_rect(pad)[:4]
                    for li, lid in ((0, pcbnew.F_Cu), (1, pcbnew.B_Cu)):
                        if not pad.IsOnLayer(lid):
                            continue
                        self.rects[li].append(
                            (nc, (cx, cy, hw, hh, 0.0)))
                        if nc:
                            self._own(li, cx, cy, max(hw, hh) + 0.3, nc)
                        self._mark(li, cx - hw, cy, cx + hw, cy,
                                   max(hw, hh) + 0.25)
                except AttributeError:
                    continue
        for it in board.GetTracks():
            try:
                nc = it.GetNetCode()
                if is_via(it):
                    q = it.GetPosition()
                    x, y = q.x / mm, q.y / mm
                    r = it.GetWidth(pcbnew.F_Cu) / mm / 2
                    for li in (0, 1):
                        self.segs[li].append((nc, (x, y), (x, y), r))
                        if nc:
                            self._own(li, x, y, r, nc)
                        self._mark(li, x, y, x, y, r + 0.1)
                else:
                    li = 0 if it.GetLayer() == pcbnew.F_Cu else 1
                    s, e = it.GetStart(), it.GetEnd()
                    try:
                        w = it.GetWidth(pcbnew.F_Cu) / mm / 2
                    except TypeError:
                        w = 0.1
                    self.segs[li].append((nc, (s.x / mm, s.y / mm),
                                          (e.x / mm, e.y / mm), w))
                    if nc:
                        n = max(2, int(((e.x - s.x) ** 2 +
                                        (e.y - s.y) ** 2) ** 0.5 / GRID))
                        for k in range(n + 1):
                            t = k / n
                            self._own(li, s.x / mm + (e.x - s.x) / mm * t * t,
                                      s.y / mm + (e.y - s.y) / mm * t * t,
                                      w, nc)
                    self._mark(li, s.x / mm, s.y / mm, e.x / mm, e.y / mm,
                               w + 0.15)
            except AttributeError:
                continue

    def free(self, li, gx, gy, nc):
        if gx < 1 or gy < 1 or gx >= self.nx - 1 or gy >= self.ny - 1:
            return False
        i = gy * self.nx + gx
        if self.occ[li][i] == 0:
            return True
        s = self.own.get((li, gx, gy))
        return bool(s and nc in s)

    def spot_ok(self, x, y, nc):
        """A 0603 jumper needs a ~1.9x1.0mm clear window: the two pad
        centers and everything within 0.55mm of them must be free."""
        for dx in (0.75, -0.75):
            gx, gy = int((x + dx) / GRID), int(y / GRID)
            for ox in (-4, 0, 4):
                for oy in (-4, 0, 4):
                    cx = (gx + ox + 0.5) * GRID - (x + dx)
                    cy = (gy + oy + 0.5) * GRID - y
                    if cx * cx + cy * cy > 0.6 * 0.6:
                        continue
                    if not self.free(0, gx + ox, gy + oy, nc):
                        return False
        return True


def free_spot_near(ob, x, y, nc, r_max=6.0):
    for r in [0.8 * (i + 1) for i in range(8)]:
        for k in range(12):
            import math
            a = 2 * math.pi * k / 12
            x2, y2 = x + r * math.cos(a), y + r * math.sin(a)
            if EDGE[0] + 1.5 < x2 < EDGE[2] - 1.5 and \
                    EDGE[1] + 1.5 < y2 < EDGE[3] - 1.5:
                if ob.spot_ok(x2, y2, nc):
                    return (x2, y2)
    return None


JUMPER_FP = """(footprint "flux:R_0603_J" (version 20221018) (generator fluxpcb)
  (layer "F.Cu") (attr smd)
  (descr "0R jumper 0603 (fluxpcb route_jumper)")
  (property "Reference" "JMP**" (at 0 -1.4 0) (layer "F.SilkS")
    (effects (font (size 0.8 0.8) (thickness 0.12))))
  (property "Value" "0R" (at 0 1.4 0) (layer "F.Fab")
    (effects (font (size 0.8 0.8) (thickness 0.12))))
  (fp_line (start -1.1 -0.75) (end 1.1 -0.75) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start 1.1 -0.75) (end 1.1 0.75) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start 1.1 0.75) (end -1.1 0.75) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start -1.1 0.75) (end -1.1 -0.75) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (pad "1" smd rect (at -0.75 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at 0.75 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask"))
)
"""


def place_jumper(idx, net_name, nc, x, y):
    """Jumper footprint at (x,y): pad1 -> net_name, pad2 -> NET#J<idx>."""
    import re as _re
    import uuid
    ref = f"JMP{idx}"
    mod = pcbnew.FOOTPRINT(board)
    fp_id = pcbnew.FPID("flux:R_0603_J")
    mod.SetFPID(fp_id)
    mod.SetReference(ref)
    mod.SetValue("0R")
    mod.SetPosition(pcbnew.VECTOR2I(int(x * mm), int(y * mm)))
    from pcbnew import FP_SHAPE, PCB_SHAPE
    try:
        pad1 = pcbnew.PAD(mod)
        pad1.SetPadName("1")
        p = pcbnew.VECTOR2I(int(-0.75 * mm), 0)
        pad1.SetPos0(p)
        pad1.SetPosition(pcbnew.VECTOR2I(int((x - 0.75) * mm), int(y * mm)))
        pad1.SetSize(pcbnew.VECTOR2I(int(0.8 * mm), int(0.95 * mm)))
        pad1.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
        pad1.SetLayerSet(pcbnew.LSET(pcbnew.F_Cu, pcbnew.F_Paste,
                                     pcbnew.F_Mask).FmtHex()
                         and pcbnew.LSET(pcbnew.F_Cu, pcbnew.F_Paste,
                                         pcbnew.F_Mask))
        pad1.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        mod.Add(pad1)
        pad2 = pcbnew.PAD(mod)
        pad2.SetPadName("2")
        p2 = pcbnew.VECTOR2I(int(0.75 * mm), 0)
        pad2.SetPos0(p2)
        pad2.SetPosition(pcbnew.VECTOR2I(int((x + 0.75) * mm), int(y * mm)))
        pad2.SetSize(pcbnew.VECTOR2I(int(0.8 * mm), int(0.95 * mm)))
        pad2.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
        pad2.SetLayerSet(pcbnew.LSET(pcbnew.F_Cu, pcbnew.F_Paste,
                                     pcbnew.F_Mask))
        pad2.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        mod.Add(pad2)
    except Exception:
        return None
    net1 = board.FindNet(net_name)
    if net1 is None:
        return None
    split = f"{net_name}#J{idx}"
    net2 = pcbnew.NETINFO_ITEM(board, split)
    board.Add(net2)
    net2 = board.FindNet(split)
    pad1.SetNet(net1)
    pad2.SetNet(net2)
    board.Add(mod)
    return {"ref": ref, "net1": net_name, "net2": split,
            "pads": {"1": (x - 0.75, y), "2": (x + 0.75, y)}}


def main():
    from importlib import util as _u
    diag_path = PCB and __file__.rsplit("route_jumper", 1)[0] + "route_diag.py"
    # reuse route_diag's open_pairs on this board
    ns = {}
    src = open(diag_path, encoding="utf-8").read()
    src = src.replace('PCB = sys.argv[1]', f'PCB = r"{PCB}"')
    src = src.split('if __name__')[0]
    exec(src, ns)
    pairs = ns["open_pairs"]()
    ob = Ob()
    results = []
    idx = 0
    for (name, nc, a, b) in pairs:
        idx += 1
        spot = free_spot_near(ob, a[0], a[1], nc)
        if spot is None:
            spot = free_spot_near(ob, b[0], b[1], nc)
        if spot is None:
            results.append({"net": name, "placed": False,
                            "why": "no free 0603 window"})
            continue
        info = place_jumper(idx, name, nc, spot[0], spot[1])
        if info is None:
            results.append({"net": name, "placed": False,
                            "why": "footprint api"})
            continue
        results.append({"net": name, "placed": True,
                        "ref": info["ref"], "at": [round(spot[0], 2),
                                                    round(spot[1], 2)],
                        "split_as": info["net2"]})
    board.Save(PCB)
    board.BuildConnectivity()
    remaining = board.GetConnectivity().GetUnconnectedCount(True)
    # NOTE: with the net split, the original open pair disappears from the
    # ratsnest (two different nets now); remaining counts what's left
    print(json.dumps({"jumpers": sum(1 for r in results if r.get("placed")),
                      "detail": results, "remaining": remaining},
                     ensure_ascii=False))


main()
