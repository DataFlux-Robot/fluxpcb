# -*- coding: utf-8 -*-
"""Collision-aware A* bridge router for the esp32s31-dev board.

Reads the board (pads/tracks/vias as obstacles, same-net copper as free),
runs DRC to get unconnected pairs, routes collision-free paths for each pair,
injects name-based-net segments+vias, then DRC-verifies in a bounded loop.
Also fixes shorting_items by removing the shorter conflicting segment and
re-routing it.
"""
from __future__ import annotations

import heapq
import math
import re
import subprocess
import sys
import uuid as uuidlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

KICAD = r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe"
GRID = 0.1           # mm per cell (= track width, so each cell IS the track)
W = 65.0             # board width mm
H = 30.0             # board height mm
NX = int(W / GRID)   # 650
NY = int(H / GRID)   # 300
CLEARANCE = 0.1      # min copper-to-copper mm (safe margin)
TRACK_W = 0.2        # new track width (= 2 grid cells wide)
VIA_COST = 20        # A* penalty for layer switch
MAX_ITER = 4


def uid():
    return str(uuidlib.uuid4())


def fmm(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


# ---------------------------------------------------------------- board parse
@dataclass
class Seg:
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str  # "F.Cu" | "B.Cu"
    net: str
    uuid: str = ""


@dataclass
class Via:
    x: float
    y: float
    net: str


@dataclass
class Pad:
    x: float
    y: float
    w: float
    h: float
    layer: str
    net: str
    thru: bool = False


def parse_board(pcb_path: Path):
    txt = pcb_path.read_text(encoding="utf-8")
    segs = []
    for m in re.finditer(
            r'\(segment\s*\(start ([\d.\-]+) ([\d.\-]+)\)\s*\(end ([\d.\-]+) ([\d.\-]+)\)'
            r'\s*\(width [\d.]+\)\s*\(layer "([^"]+)"\)\s*\(net "([^"]+)"\)', txt):
        x1, y1, x2, y2, layer, net = m.groups()
        segs.append(Seg(float(x1), float(y1), float(x2), float(y2), layer, net))
    vias = []
    for m in re.finditer(
            r'\(via\s*\(at ([\d.\-]+) ([\d.\-]+)[\s\S]{0,200}?\(net "([^"]+)"\)', txt):
        x, y, net = m.groups()
        vias.append(Via(float(x), float(y), net))
    pads = []

    # find all footprints with their position AND rotation
    fp_matches = list(re.finditer(
        r'\(footprint\s+"[^"]*"', txt))
    fp_info = []
    for fm in fp_matches:
        start = fm.start()
        # find the (at x y rot) right after the footprint name block
        at_m = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.\-]+))?\)',
                         txt[start:start + 500])
        if at_m:
            fx, fy = float(at_m.group(1)), float(at_m.group(2))
            frot = float(at_m.group(3)) if at_m.group(3) else 0.0
            fp_info.append((start, fx, fy, frot))
        else:
            fp_info.append((start, 0.0, 0.0, 0.0))

    for m in re.finditer(
            r'\(pad "[^"]+"\s+(\w+)\s+(\w+)\s*\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.\-]+))?\)'
            r'\s*\(size ([\d.]+) ([\d.]+)\)[\s\S]{0,300}?\(net "([^"]+)"\)', txt):
        ptype, pshape, lx, ly, _padrot, w, h, net = m.groups()
        # find enclosing footprint
        pad_start = m.start()
        enclosing = None
        for fp_start, fx, fy, frot in fp_info:
            if fp_start < pad_start:
                enclosing = (fx, fy, frot)
            else:
                break
        if enclosing is None:
            enclosing = (0.0, 0.0, 0.0)
        fx, fy, frot = enclosing

        # apply footprint rotation to local pad coordinates
        lx, ly = float(lx), float(ly)
        rad = math.radians(frot)
        if abs(frot - 90) < 1:
            rx, ry = ly, -lx
        elif abs(frot - 180) < 1:
            rx, ry = -lx, -ly
        elif abs(frot - 270) < 1:
            rx, ry = -ly, lx
        else:
            rx, ry = lx * math.cos(rad) - ly * math.sin(rad), \
                     lx * math.sin(rad) + ly * math.cos(rad)

        gx, gy = fx + rx, fy + ry

        # swap pad w/h if rotation is 90/270
        pw, ph = float(w), float(h)
        if abs(frot - 90) < 1 or abs(frot - 270) < 1:
            pw, ph = ph, pw

        thru = "thru_hole" in ptype
        layer = "F.Cu" if not thru else "BOTH"
        seg_txt = txt[m.start():m.start() + 500]
        if '"B.Cu"' in seg_txt.split("(net")[0] and not thru:
            layer = "B.Cu"
        pads.append(Pad(gx, gy, pw, ph, layer, net, thru))
    return segs, vias, pads


# ---------------------------------------------------------------- obstacle map
def build_obstacle_grid(segs, vias, pads, target_net: str):
    """Two boolean grids [2][NY][NX]: True = blocked for target_net routing."""
    grids = np.zeros((2, NY, NX), dtype=bool)
    # same-net items are free; others are blocked with clearance inflation
    inflate = CLEARANCE + TRACK_W / 2  # 0.09 + 0.1 = 0.19mm

    def block_circle(g, cx, cy, r):
        ix, iy = int(cx / GRID), int(cy / GRID)
        ir = int(r / GRID) + 1
        for dy in range(-ir, ir + 1):
            for dx in range(-ir, ir + 1):
                x, y = ix + dx, iy + dy
                if 0 <= x < NX and 0 <= y < NY:
                    if (dx * GRID) ** 2 + (dy * GRID) ** 2 <= r ** 2:
                        g[y, x] = True

    def block_thick_line(g, x1, y1, x2, y2, half_w):
        # rasterize thick line by sampling
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 0.01:
            block_circle(g, x1, y1, half_w)
            return
        steps = max(int(length / (GRID * 0.5)), 2)
        for i in range(steps + 1):
            t = i / steps
            px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            block_circle(g, px, py, half_w)

    li = {"F.Cu": 0, "B.Cu": 1}

    for seg in segs:
        if seg.net == target_net:
            continue
        half = TRACK_W / 2 + CLEARANCE  # inflate by half-track + clearance
        for layer in ([seg.layer] if seg.layer in li else ["F.Cu", "B.Cu"]):
            block_thick_line(grids[li[layer]], seg.x1, seg.y1, seg.x2, seg.y2, half)

    for via in vias:
        if via.net == target_net:
            continue
        r = 0.3 + CLEARANCE  # via radius + clearance
        for g in grids:
            block_circle(g, via.x, via.y, r)

    for pad in pads:
        if pad.net == target_net:
            continue
        half = max(pad.w, pad.h) / 2 + CLEARANCE
        layers = [0, 1] if pad.thru or pad.layer == "BOTH" else \
            ([li[pad.layer]] if pad.layer in li else [0])
        for l in layers:
            block_circle(grids[l], pad.x, pad.y, half)

    return grids


# ---------------------------------------------------------------- A* search
MAX_STEPS = 200000  # exploration cap


def astar(grids, start, goal, start_layer=0, goal_layer=0):
    """A* on 2-layer grid, numpy-backed. Returns list of (x_mm, y_mm, layer) or None."""
    sx, sy = int(start[0] / GRID), int(start[1] / GRID)
    gx, gy = int(goal[0] / GRID), int(goal[1] / GRID)
    # clip to grid
    sx, sy = max(0, min(NX - 1, sx)), max(0, min(NY - 1, sy))
    gx, gy = max(0, min(NX - 1, gx)), max(0, min(NY - 1, gy))

    # numpy dist arrays: [2 layers][NY][NX] float32
    INF = np.float32(1e9)
    dist = np.full((2, NY, NX), INF, dtype=np.float32)
    prev = {}  # sparse: only for reconstructing path
    open_heap = []

    def h(l, x, y):
        return abs(x - gx) + abs(y - gy) + (VIA_COST if l != goal_layer else 0)

    dist[start_layer, sy, sx] = 0
    heapq.heappush(open_heap, (h(start_layer, sx, sy), 0, (start_layer, sx, sy)))

    DIRS = [(0, 1), (0, -1), (1, 0), (-1, 0)]
    steps = 0

    while open_heap:
        _, _, state = heapq.heappop(open_heap)
        l, x, y = state
        if x == gx and y == gy and l == goal_layer:
            path = []
            s = state
            while s in prev:
                path.append((s[1] * GRID, s[2] * GRID, s[0]))
                s = prev[s]
            path.append((sx * GRID, sy * GRID, start_layer))
            path.reverse()
            return path
        d0 = float(dist[l, y, x])
        if d0 > 1e8:
            continue
        steps += 1
        if steps > MAX_STEPS:
            return None
        for dx, dy in DIRS:
            nx_, ny_ = x + dx, y + dy
            if not (0 <= nx_ < NX and 0 <= ny_ < NY):
                continue
            if grids[l][ny_, nx_]:
                continue
            nd = d0 + 1.0
            if nd < dist[l, ny_, nx_]:
                dist[l, ny_, nx_] = nd
                ns = (l, nx_, ny_)
                prev[ns] = state
                heapq.heappush(open_heap, (nd + h(l, nx_, ny_), nd, ns))
        # layer switch (via)
        other = 1 - l
        if not grids[other][y, x]:
            nd = d0 + VIA_COST
            if nd < dist[other, y, x]:
                dist[other, y, x] = nd
                ns = (other, x, y)
                prev[ns] = state
                heapq.heappush(open_heap, (nd + h(other, x, y), nd, ns))
    return None


def simplify_path(path):
    """Grid path -> list of Manhattan segments [(x1,y1,x2,y2,layer), via_flags]."""
    if not path or len(path) < 2:
        return [], []
    segs = []
    vias = []
    # detect layer changes → via points
    layer_change_pts = []
    for i in range(1, len(path)):
        if path[i][2] != path[i - 1][2]:
            layer_change_pts.append((i, path[i - 1], path[i]))

    # build segment list between corner points
    pts = path
    corners = [0]
    for i in range(1, len(pts) - 1):
        dx1 = pts[i][0] - pts[i - 1][0]
        dy1 = pts[i][1] - pts[i - 1][1]
        dx2 = pts[i + 1][0] - pts[i][0]
        dy2 = pts[i + 1][1] - pts[i][1]
        if abs(dx1) < 1e-9 and abs(dx2) < 1e-9:
            continue  # collinear vertical
        if abs(dy1) < 1e-9 and abs(dy2) < 1e-9:
            continue  # collinear horizontal
        if abs(dx1 - dx2) < 1e-9 and abs(dy1 - dy2) < 1e-9:
            continue  # collinear diagonal
        corners.append(i)
    corners.append(len(pts) - 1)

    for i in range(len(corners) - 1):
        a = pts[corners[i]]
        b = pts[corners[i + 1]]
        if a[2] != b[2]:
            # layer change: via at a, then segment on b's layer
            vias.append((a[0], a[1]))
            segs.append((a[0], a[1], b[0], b[1], "F.Cu" if b[2] == 0 else "B.Cu"))
        else:
            segs.append((a[0], a[1], b[0], b[1], "F.Cu" if a[2] == 0 else "B.Cu"))
    return segs, vias


# ---------------------------------------------------------------- DRC report parse
def run_drc(pcb: Path, rpt: Path) -> str:
    p = subprocess.run([KICAD, "pcb", "drc", "--exit-code-violations",
                        "--refill-zones", "--save-board", "-o", str(rpt), str(pcb)],
                       capture_output=True, text=True, timeout=900)
    return rpt.read_text(encoding="utf-8", errors="replace")


def parse_pairs(txt: str, kind="unconnected_items"):
    pairs = []
    for m in re.finditer(
            rf"\[{kind}\][^\n]*\n[^\n]*\n\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)\n"
            rf"\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)", txt):
        g = m.groups()
        pairs.append((float(g[0]), float(g[1]), g[2].strip(),
                      float(g[3]), float(g[4]), g[5].strip()))
    return pairs


def net_of(desc):
    m = re.search(r"\[(\S+)\]", desc)
    return m.group(1) if m else None


# ---------------------------------------------------------------- main repair
def main(pcb_path: Path):
    rpt = pcb_path.parent / "drc.rpt"
    for it in range(1, MAX_ITER + 1):
        txt = run_drc(pcb_path, rpt)
        unconn = parse_pairs(txt, "unconnected_items")
        shorts = parse_pairs(txt, "shorting_items")
        print(f"iter {it}: unconnected={len(unconn)} shorts={len(shorts)}")

        if not unconn and not shorts:
            print("✅ ALL CLOSED — DRC zero")
            return 0

        segs, vias, pads = parse_board(pcb_path)
        new_items = []

        # --- fix shorts: remove shorter conflicting segment
        for x1, y1, d1, x2, y2, d2 in shorts:
            n1, n2 = net_of(d1), net_of(d2)
            if not n1 or not n2 or n1 == n2:
                continue
            # find segments matching the reported coords; remove the shorter one
            board_txt = pcb_path.read_text(encoding="utf-8")
            removed = False
            for seg in sorted(segs, key=lambda s: abs(s.x1 - s.x2) + abs(s.y1 - s.y2)):
                if seg.net in (n1, n2):
                    # match by proximity to reported coords
                    if (abs(seg.x1 - x1) < 0.5 and abs(seg.y1 - y1) < 0.5) or \
                       (abs(seg.x2 - x1) < 0.5 and abs(seg.y2 - y1) < 0.5):
                        # remove this segment from board text
                        pat = re.compile(
                            r'\(segment\s*\(start ' + re.escape(fmm(seg.x1)) +
                            r' ' + re.escape(fmm(seg.y1)) + r'\)\s*\(end ' +
                            re.escape(fmm(seg.x2)) + r' ' + re.escape(fmm(seg.y2)) +
                            r'\)[\s\S]{0,200}?\(net "' + re.escape(seg.net) + r'"\)[\s\S]{0,100}?\)')
                        new_txt, n = pat.subn("", board_txt)
                        if n > 0:
                            board_txt = new_txt
                            print(f"  short-fix: removed {seg.net} seg "
                                  f"({seg.x1:.2f},{seg.y1:.2f})-({seg.x2:.2f},{seg.y2:.2f})")
                            removed = True
                            break
            if removed:
                pcb_path.write_text(board_txt, encoding="utf-8")
            continue  # re-parse board next iteration after removal

        # --- route unconnected pairs with A* (INCREMENTAL obstacle updates)
        for x1, y1, d1, x2, y2, d2 in unconn:
            n1 = net_of(d1)
            if not n1:
                continue
            l1 = 1 if "bottom" in d1 else 0
            l2 = 1 if "bottom" in d2 else 0

            grids = build_obstacle_grid(segs, vias, pads, n1)
            path = astar(grids, (x1, y1), (x2, y2), start_layer=l1, goal_layer=l2)
            if path is None:
                path = astar(grids, (x1, y1), (x2, y2),
                             start_layer=1 - l1, goal_layer=1 - l2)
            if path is None:
                print(f"  ❌ A* failed for {n1}: ({x1:.2f},{y1:.2f})→({x2:.2f},{y2:.2f})")
                continue
            path_segs, path_vias = simplify_path(path)
            for sx1, sy1, sx2, sy2, layer in path_segs:
                new_items.append(
                    f'\t(segment (start {fmm(sx1)} {fmm(sy1)}) (end {fmm(sx2)} {fmm(sy2)}) '
                    f'(width {TRACK_W}) (layer "{layer}") (net "{n1}") '
                    f'(uuid "{uid()}"))')
                # INCREMENTAL: add to obstacle set for subsequent routes
                segs.append(Seg(sx1, sy1, sx2, sy2, layer, n1))
            for vx, vy in path_vias:
                new_items.append(
                    f'\t(via (at {fmm(vx)} {fmm(vy)}) (size 0.6) (drill 0.3) '
                    f'(layers "F.Cu" "B.Cu") (net "{n1}") (uuid "{uid()}"))')
                vias.append(Via(vx, vy, n1))
            print(f"  ✅ routed {n1}: {len(path_segs)} segs, {len(path_vias)} vias")

        if new_items:
            board_txt = pcb_path.read_text(encoding="utf-8").rstrip()
            board_txt = board_txt[:-1] + "\n" + "\n".join(new_items) + "\n)\n"
            pcb_path.write_text(board_txt, encoding="utf-8")
        elif not shorts:
            print("no progress possible; stopping")
            return 1

    return 0 if not unconn and not shorts else 1


if __name__ == "__main__":
    pcb = Path(sys.argv[1] if len(sys.argv) > 1 else
               "projects/esp32s31-dev/pcb/board/esp32s31-dev.kicad_pcb")
    sys.exit(main(pcb))
