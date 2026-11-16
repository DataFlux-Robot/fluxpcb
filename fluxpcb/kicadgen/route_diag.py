# -*- coding: utf-8 -*-
"""Quantified bottleneck diagnosis for residual nets — absorbs
hyuhang822-blip/kicad-sch-to-pcb references/diagnosis.md:

    true gap -> connectivity islands -> corridor width profile ->
    BIDIRECTIONAL FLOOD to localize the bottleneck -> when to stop
    and hand over (or insert a jumper, which route_jumper.py does).

For every still-open pair the report answers, machine-readably:
  WHERE  the bottleneck is (meeting point of the two floods)
  HOW WIDE the channel is there (free-cell count across the pinch)
  WHAT blocks it (pads / tracks / vias / hole-keepouts, per culprit ref)

Runs inside KiCad Python:
    python route_diag.py PCB CLSFILE [OUT.json]
Prints one JSON line: {net, bottleneck_mm, at, blockers[], verdict}
verdict: "routable" (width>=3 cells) | "tight" (1-2) | "jumper"
(the jumper verdict feeds the deterministic 0R closure).
"""
import heapq
import json
import sys

import pcbnew

PCB = sys.argv[1]
CLS = sys.argv[2] if len(sys.argv) > 2 else ""
OUT = sys.argv[3] if len(sys.argv) > 3 else ""
mm = 1000000
board = pcbnew.LoadBoard(PCB)

try:
    NET_CLASS = json.load(open(CLS)).get("classes", {})
except (OSError, ValueError):
    NET_CLASS = {}

GRID = 0.16
CLEAR = 0.17
VIA_ANNULUS = 0.25

_bb = board.GetBoardEdgesBoundingBox()
EDGE = (_bb.GetX() / mm, _bb.GetY() / mm,
        (_bb.GetX() + _bb.GetWidth()) / mm,
        (_bb.GetY() + _bb.GetHeight()) / mm)

_PTH = getattr(pcbnew, "PAD_ATTRIB_PTH",
               getattr(pcbnew, "PAD_ATTRIB_THROUGH_HOLE", 3))


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
    ang = pad.GetOrientation()
    try:
        ang = ang.AsRadians()
    except AttributeError:
        ang = float(ang or 0.0)
    return (p.x / mm, p.y / mm, w / 2.0, h / 2.0, ang)


# ------------------------------------------------------------- model
class Diag:
    """Shared obstacle model (same semantics as route_session.Obstacles,
    trimmed to diagnosis needs)."""

    def __init__(self):
        self.nx = int((EDGE[2] - EDGE[0]) / GRID) + 2
        self.ny = int((EDGE[3] - EDGE[1]) / GRID) + 2
        self.occ = [bytearray(self.nx * self.ny) for _ in range(2)]
        self.own = {}                       # (li,gx,gy) -> set(nc)
        self.culprit = {}                   # blocked cell -> what blocks it
        self._raster()

    def _mark(self, li, x0, y0, x1, y1, margin, why):
        gx0 = int((min(x0, x1) - margin) / GRID)
        gx1 = int((max(x0, x1) + margin) / GRID) + 1
        gy0 = int((min(y0, y1) - margin) / GRID)
        gy1 = int((max(y0, y1) + margin) / GRID) + 1
        for gx in range(max(0, gx0), min(self.nx, gx1)):
            cx = (gx + 0.5) * GRID
            for gy in range(max(0, gy0), min(self.ny, gy1)):
                cy = (gy + 0.5) * GRID
                if pt_seg_dist(cx, cy, x0, y0, x1, y1) <= margin:
                    i = gy * self.nx + gx
                    self.occ[li][i] = 1
                    self.culprit.setdefault((li, gx, gy), []).append(why)

    def _raster(self):
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                try:
                    nc = pad.GetNetCode()
                    cx, cy, hw, hh, ang = pad_rect(pad)
                    ref = fp.GetReferenceAsString() + "/" + str(pad.GetPadName())
                    why = ("pad", ref, nc)
                    for li, lid in ((0, pcbnew.F_Cu), (1, pcbnew.B_Cu)):
                        if not pad.IsOnLayer(lid):
                            continue
                        if nc:
                            self._own_mark(li, cx, cy, max(hw, hh) + 0.3, nc)
                        self._mark(li, cx - hw, cy, cx + hw, cy,
                                   max(hw, hh) + 0.25, why)
                except AttributeError:
                    continue
        for it in board.GetTracks():
            try:
                nc = it.GetNetCode()
                if is_via(it):
                    q = it.GetPosition()
                    x, y = q.x / mm, q.y / mm
                    r = it.GetWidth(pcbnew.F_Cu) / mm / 2
                    why = ("via", f"V({it.GetNetname()})", nc)
                    for li in (0, 1):
                        if nc:
                            self._own_mark(li, x, y, r, nc)
                        self._mark(li, x, y, x, y, r + 0.1, why)
                else:
                    li = 0 if it.GetLayer() == pcbnew.F_Cu else 1
                    s, e = it.GetStart(), it.GetEnd()
                    try:
                        w = it.GetWidth(pcbnew.F_Cu) / mm / 2
                    except TypeError:
                        try:
                            w = it.GetWidth() / mm / 2
                        except TypeError:
                            w = 0.1
                    why = ("track", f"T({it.GetNetname()})", nc)
                    if nc:
                        self._own_seg(li, s.x / mm, s.y / mm, e.x / mm, e.y / mm, w, nc)
                    self._mark(li, s.x / mm, s.y / mm, e.x / mm, e.y / mm,
                               w + 0.15, why)
            except AttributeError:
                continue

    def _own_mark(self, li, x, y, r, nc):
        for gx in range(int((x - r) / GRID), int((x + r) / GRID) + 1):
            for gy in range(int((y - r) / GRID), int((y + r) / GRID) + 1):
                cx, cy = (gx + 0.5) * GRID, (gy + 0.5) * GRID
                if (cx - x) ** 2 + (cy - y) ** 2 <= r * r:
                    self.own.setdefault((li, gx, gy), set()).add(nc)

    def _own_seg(self, li, x0, y0, x1, y1, w, nc):
        n = max(2, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / GRID))
        for k in range(n + 1):
            t = k / n
            self._own_mark(li, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, w, nc)

    def idx(self, gx, gy):
        if gx < 1 or gy < 1 or gx >= self.nx - 1 or gy >= self.ny - 1:
            return -1
        return gy * self.nx + gx

    def free(self, li, gx, gy, nc):
        i = self.idx(gx, gy)
        if i < 0:
            return False
        if self.occ[li][i] == 0:
            return True
        s = self.own.get((li, gx, gy))
        return bool(s and nc in s)


# ------------------------------------------------------------- floods
def flood(dm, nc, start, limit_cells=250000):
    """Weighted flood from one anchor: gscore over the free grid (both
    layers, via moves cost extra). Returns {cell: g} and parents."""
    li0, gx0, gy0 = start
    heap = [(0.0, 0, start)]
    gscore = {start: 0.0}
    parent = {}
    VIA_COST = 8.0
    count = 0
    while heap:
        _, _, node = heapq.heappop(heap)
        lay, gx, gy = node
        cur = gscore[node]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            gx2, gy2 = gx + dx, gy + dy
            if not dm.free(lay, gx2, gy2, nc):
                continue
            step = GRID * (1.4142 if dx and dy else 1.0)
            nk = (lay, gx2, gy2)
            if gscore.get(nk, 1e18) > cur + step:
                gscore[nk] = cur + step
                parent[nk] = node
                count += 1
                heapq.heappush(heap, (cur + step, count, nk))
        nk = (1 - lay, gx, gy)
        if dm.free(1 - lay, gx, gy, nc) and gscore.get(nk, 1e18) > cur + VIA_COST:
            gscore[nk] = cur + VIA_COST
            parent[nk] = node
            count += 1
            heapq.heappush(heap, (cur + VIA_COST, count, nk))
        if count > limit_cells:
            break
    return gscore, parent


def channel_width(dm, li, gx, gy, nc):
    """Free cells across the pinch at the meeting point: scan the 4
    orthogonal+diagonal rays until blocked, width = farthest pair span."""
    best = 0
    for (dx, dy) in ((1, 0), (0, 1), (1, 1)):
        a = b = 0
        x, y = gx, gy
        while dm.free(li, x + dx, y + dy, nc):
            x += dx
            y += dy
            a += 1
        x, y = gx, gy
        while dm.free(li, x - dx, y - dy, nc):
            x -= dx
            y -= dy
            b += 1
        best = max(best, a + b + 1)
    return best


def anchor_cell(dm, x, y, lay, nc):
    gx, gy = int(x / GRID), int(y / GRID)
    for r in range(0, 6):
        for dx in range(-r, r + 1):
            for dy in (range(-r, r + 1) if abs(dx) == r else (0,)):
                if dm.free(lay, gx + dx, gy + dy, nc):
                    return (lay, gx + dx, gy + dy)
    return None


# ------------------------------------------------------------- audit
def open_pairs():
    """(net, nc, a, b) per fragmented net — same island/union-find model
    as conn_audit (position keys; wrapper ids are unstable)."""
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
        name = pads[0].GetNetname()
        isl = list(islands.values())
        best = None
        for i in range(len(isl)):
            for j in range(i + 1, len(isl)):
                for pa in isl[i]:
                    for pb in isl[j]:
                        qa, qb = pa.GetPosition(), pb.GetPosition()
                        d2 = (qa.x - qb.x) ** 2 + (qa.y - qb.y) ** 2
                        if best is None or d2 < best[0]:
                            best = (d2, qa, qb)
        _, qa, qb = best
        out.append((name, nc,
                    (qa.x / mm, qa.y / mm, 0),
                    (qb.x / mm, qb.y / mm, 0)))
    return out


# ------------------------------------------------------------- main
def diagnose():
    dm = Diag()
    pairs = open_pairs()
    report = []
    for (name, nc, a, b) in pairs:
        ca = anchor_cell(dm, a[0], a[1], 0, nc)
        cb = anchor_cell(dm, b[0], b[1], 0, nc)
        entry = {"net": name}
        if ca is None or cb is None:
            entry.update({"verdict": "sealed-anchor",
                          "note": "no free cell near a pad anchor"})
            report.append(entry)
            continue
        ga, _ = flood(dm, nc, ca)
        gb, _ = flood(dm, nc, cb)
        # meeting point: the reachable cell pair with min combined g
        meet = None
        small, big = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
        for cell, g in small.items():
            g2 = big.get(cell)
            if g2 is not None:
                tot = g + g2
                if meet is None or tot < meet[0]:
                    meet = (tot, cell)
        if meet is None:
            entry.update({"verdict": "disconnected-components",
                          "comp_a": len(ga), "comp_b": len(gb)})
            report.append(entry)
            continue
        _, (li, gx, gy) = meet
        width = channel_width(dm, li, gx, gy, nc)
        blockers = []
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for wh in dm.culprit.get((li, gx + ddx, gy + ddy), [])[:1]:
                    if wh[2] != nc:
                        blockers.append(wh[1])
        entry.update({
            "at": [round((gx + 0.5) * GRID, 2), round((gy + 0.5) * GRID, 2)],
            "layer": "F" if li == 0 else "B",
            "width_cells": width,
            "width_mm": round(width * GRID, 2),
            "blockers": sorted(set(blockers))[:4],
            "verdict": "routable" if width >= 3 else ("tight" if width >= 1
                                                      else "jumper"),
        })
        report.append(entry)
    return report


if __name__ == "__main__":
    rep = diagnose()
    txt = json.dumps(rep, ensure_ascii=False)
    if OUT:
        open(OUT, "w", encoding="utf-8").write(txt)
    summary = {}
    for e in rep:
        summary[e.get("verdict", "?")] = summary.get(e.get("verdict", "?"), 0) + 1
    print(json.dumps({"pairs": len(rep), "verdicts": summary,
                      "detail": rep[:6]}, ensure_ascii=False))
