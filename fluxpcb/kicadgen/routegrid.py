# -*- coding: utf-8 -*-
"""Shared routing-grid core — the single source of truth for the grid
model that route_session.Obstacles, route_diag.Diag and route_embedded
each carried as a private copy (three drift-prone implementations of the
same physics; route_embedded even drifted to a coarser grid).

Extracted with the exact verified semantics from route_session (K7-K13
lessons baked in):
  - honest pad margins: extent + clearance(0.15) + track half (K7: loose
    margins made the A* chase phantom diagonals)
  - own-net copper passability keyed by netcode (K11)
  - via_block/hole_block per the 0.4/0.2 dog-bone lattice (K12)
  - per-step emission contract helpers (add_track/add_via) and the
    island-BFS pair_connected verifier (immune to zone-refill noise)

Z-round (research-backed) upgrades:
  - Z2: all tunables come from knowhow_constraints.json via
    route_constraints.load() — no more drifted hardcoded duplicates
  - Z3: track raster margin is the CONSTRUCTIVE bound (w + track_margin)
    so "grid-free => clear_exact passes" holds by construction instead
    of being vetoed after the fact (fc's dominant physical failures)
  - Z4: GridModel models N signal layers (layer_ids list) and rasterizes
    filled ZONE copper — inner-layer routing was previously blind to the
    GND pours on In1/In4 (guaranteed shorts)

Importable from sibling KiCad-Python scripts:
    sys.path.insert(0, os.path.dirname(__file__)); import routegrid
"""
from __future__ import annotations

import pcbnew

sys = __import__("sys")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import route_constraints as _RC          # noqa: E402

CONS = _RC.load()

MM = 1000000
GRID = CONS["grid"]["resolution_mm"]
CLEAR_MM = CONS["grid"]["clearance_mm"]           # 0.17: 0.15 DRC + margin
PAD_MARGIN = CONS["grid"]["pad_margin_mm"]        # 0.30 beyond pad extent
TRACK_MARGIN = CONS["grid"]["track_margin_mm"]    # Z3: beyond track half-w
VIA_ANNULUS = CONS["grid"]["via_annulus_mm"]      # via radius (0.5 via)
VIA_DRILL = CONS["grid"]["via_drill_mm"]          # drill radius (0.25 dia)
HOLE_KEEP = CONS["grid"]["hole_keep_mm"]
EDGE_KEEP = CONS["grid"]["edge_keep_mm"]

_PTH = getattr(pcbnew, "PAD_ATTRIB_PTH",
               getattr(pcbnew, "PAD_ATTRIB_THROUGH_HOLE", 3))

_LAYER_BY_NAME = {"F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu}


def signal_layer_ids(board, cons=None):
    """pcbnew layer ids of routable signal layers for this board.

    From constraints stackup signal_layers, filtered by the board's
    actual copper count: 2-layer boards keep [F, B]; 6-layer fc keeps
    [F, In1, In4, B] (In2/In3 are power planes and stay excluded).
    """
    cons = cons or CONS
    names = cons.get("stackup_6l", {}).get("signal_layers",
                                           ["F.Cu", "B.Cu"])
    ncopper = board.GetCopperLayerCount()
    ids = []
    for nm in names:
        lid = None
        if nm == "F.Cu":
            lid = pcbnew.F_Cu
        elif nm == "B.Cu":
            lid = pcbnew.B_Cu
        elif nm.startswith("In") and nm.endswith(".Cu"):
            k = int(nm[2:-3])
            if 1 <= k <= ncopper - 2:          # inner layers that exist
                lid = getattr(pcbnew, "In%d_Cu" % k, None)
        if lid is not None and lid not in ids:
            ids.append(lid)
    if pcbnew.B_Cu not in ids:
        ids.append(pcbnew.B_Cu)
    if pcbnew.F_Cu not in ids:
        ids.insert(0, pcbnew.F_Cu)
    return ids


def pt_seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def seg_seg_dist(a1, a2, b1, b2):
    (x1, y1), (x2, y2) = a1, a2
    (x3, y3), (x4, y4) = b1, b2
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-12:
        return min(pt_seg_dist(x1, y1, x3, y3, x4, y4),
                   pt_seg_dist(x2, y2, x3, y3, x4, y4),
                   pt_seg_dist(x3, y3, x1, y1, x2, y2),
                   pt_seg_dist(x4, y4, x1, y1, x2, y2))
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if 0 <= t <= 1 and 0 <= u <= 1:
        return 0.0
    return min(pt_seg_dist(x1, y1, x3, y3, x4, y4),
               pt_seg_dist(x2, y2, x3, y3, x4, y4),
               pt_seg_dist(x3, y3, x1, y1, x2, y2),
               pt_seg_dist(x4, y4, x1, y1, x2, y2))


def is_via(item):
    return item.Type() == pcbnew.PCB_VIA_T


def track_w(item):
    try:
        return item.GetWidth(pcbnew.F_Cu) / MM / 2
    except TypeError:
        try:
            return item.GetWidth() / MM / 2
        except TypeError:
            return 0.1


def pad_rect(pad):
    p = pad.GetPosition()
    try:
        sz = pad.GetSize()
        w, h = sz.x / MM, sz.y / MM
    except AttributeError:
        bb = pad.GetBoundingBox()
        w, h = bb.GetWidth() / MM, bb.GetHeight() / MM
    ang = pad.GetOrientation()
    try:
        ang = ang.AsRadians()
    except AttributeError:
        ang = float(ang or 0.0)
    return (p.x / MM, p.y / MM, w / 2.0, h / 2.0, ang)


def pt_rect_dist(px, py, rect):
    import math
    cx, cy, hw, hh, ang = rect
    if ang:
        ca, sa = math.cos(ang), math.sin(ang)
        dx, dy = px - cx, py - cy
        rx = dx * ca + dy * sa
        ry = -dx * sa + dy * ca
    else:
        rx, ry = px - cx, py - cy
    ex = max(abs(rx) - hw, 0.0)
    ey = max(abs(ry) - hh, 0.0)
    return (ex * ex + ey * ey) ** 0.5


def _shape_outlines(polys):
    """SHAPE_POLY_SET outline vertex lists [[(x_mm, y_mm), ...], ...].

    Defensive against KiCad 10 SWIG shape API variations; returns [] if
    unreachable (caller falls back to bbox).
    """
    out = []
    try:
        n = polys.OutlineCount()
    except (AttributeError, TypeError):
        return []
    for i in range(n):
        try:
            chain = polys.Outline(i)
            pts = []
            np_ = chain.PointCount()
            for j in range(np_):
                v = chain.CPoint(j) if hasattr(chain, "CPoint") \
                    else chain.GetPoint(j)
                pts.append((v.x / MM, v.y / MM))
            if len(pts) >= 3:
                out.append(pts)
        except (AttributeError, TypeError):
            return []
    return out


def _zone_outlines(zone):
    try:
        return _shape_outlines(zone.GetFilledPolysList())
    except (AttributeError, TypeError):
        return []


def _pip(px, py, pts):
    """Even-odd point-in-polygon."""
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > py) != (yj > py):
            xc = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < xc:
                inside = not inside
        j = i
    return inside


class GridModel:
    """Obstacle/occupancy model over an (nx x ny x N-layer) lattice.

    layer_ids: pcbnew layer ids modeled (default [F, B]; fluxrouter passes
    the stackup signal layers for 3D routing). occ: 0 free / 1 blocked
    (fixed copper: pads + committed tracks + zone copper). own_cells:
    (li,gx,gy) -> set(netcodes) — same-net pass-through. own_via_cells:
    (gx,gy) -> set(netcodes) — free layer bridges.
    """

    def __init__(self, board, layer_ids=None):
        self.board = board
        self.layer_ids = list(layer_ids) if layer_ids else \
            [pcbnew.F_Cu, pcbnew.B_Cu]
        self.nl = len(self.layer_ids)
        self.edge = self._board_edge()
        self.nx = int((self.edge[2] - self.edge[0]) / GRID) + 2
        self.ny = int((self.edge[3] - self.edge[1]) / GRID) + 2
        ncell = self.nx * self.ny
        self.occ = [bytearray(ncell) for _ in range(self.nl)]
        self.via_block = [bytearray(ncell) for _ in range(self.nl)]
        self.hole_block = bytearray(ncell)
        self.own_cells = {}
        self.own_via_cells = {}
        self.segs = tuple([] for _ in range(self.nl))  # (netcode,a,b,half_w)
        self.rects = tuple([] for _ in range(self.nl))  # (netcode, rect)
        self.routed = {}                # cell -> netcode (router-placed)
        self.zone_stats = {"zones": 0, "cells": 0, "bbox_fallback": 0}
        self._raster_fixed()
        self._raster_zones()

    def _board_edge(self):
        bb = self.board.GetBoardEdgesBoundingBox()
        return (bb.GetX() / MM, bb.GetY() / MM,
                (bb.GetX() + bb.GetWidth()) / MM,
                (bb.GetY() + bb.GetHeight()) / MM)

    def li_of(self, lid):
        """Router layer index for a pcbnew layer id (-1 if unmodeled)."""
        try:
            return self.layer_ids.index(lid)
        except ValueError:
            return -1

    def idx(self, gx, gy):
        if gx < 1 or gy < 1 or gx >= self.nx - 1 or gy >= self.ny - 1:
            return -1
        return gy * self.nx + gx

    # ------------------------------------------------- fixed rasterization
    def _mark(self, li, x0, y0, x1, y1, margin, via_r=None):
        pad = 0.15          # box slack so cell centers near the margin edge
        gx0 = int((min(x0, x1) - margin - pad) / GRID)
        gx1 = int((max(x0, x1) + margin + pad) / GRID) + 1
        gy0 = int((min(y0, y1) - margin - pad) / GRID)
        gy1 = int((max(y0, y1) + margin + pad) / GRID) + 1
        m2 = max(margin, via_r or 0)
        bx0 = int((min(x0, x1) - m2) / GRID)
        bx1 = int((max(x0, x1) + m2) / GRID) + 1
        by0 = int((min(y0, y1) - m2) / GRID)
        by1 = int((max(y0, y1) + m2) / GRID) + 1
        occ, vb = self.occ[li], self.via_block[li]
        for gx in range(max(0, bx0), min(self.nx, bx1)):
            cx = (gx + 0.5) * GRID
            for gy in range(max(0, by0), min(self.ny, by1)):
                cy = (gy + 0.5) * GRID
                d = pt_seg_dist(cx, cy, x0, y0, x1, y1)
                i = gy * self.nx + gx
                if d <= margin:
                    occ[i] = 1
                if via_r and d <= via_r:
                    vb[i] = 1

    def _mark_holes(self, x, y, r):
        gx0, gx1 = int((x - r) / GRID), int((x + r) / GRID) + 1
        gy0, gy1 = int((y - r) / GRID), int((y + r) / GRID) + 1
        for gx in range(max(0, gx0), min(self.nx, gx1)):
            for gy in range(max(0, gy0), min(self.ny, gy1)):
                cx, cy = (gx + 0.5) * GRID, (gy + 0.5) * GRID
                if (cx - x) ** 2 + (cy - y) ** 2 <= r * r:
                    self.hole_block[gy * self.nx + gx] = 1

    def _mark_own(self, li, x0, y0, x1, y1, half_w, nc):
        gx0 = int((min(x0, x1) - half_w) / GRID)
        gx1 = int((max(x0, x1) + half_w) / GRID) + 1
        gy0 = int((min(y0, y1) - half_w) / GRID)
        gy1 = int((max(y0, y1) + half_w) / GRID) + 1
        for gx in range(max(0, gx0), min(self.nx, gx1)):
            cx = (gx + 0.5) * GRID
            for gy in range(max(0, gy0), min(self.ny, gy1)):
                cy = (gy + 0.5) * GRID
                if pt_seg_dist(cx, cy, x0, y0, x1, y1) <= half_w:
                    self.own_cells.setdefault((li, gx, gy), set()).add(nc)

    def _raster_fixed(self):
        for fp in self.board.GetFootprints():
            for pad in fp.Pads():
                try:
                    nc = pad.GetNetCode()
                    cx, cy, hw, hh, ang = pad_rect(pad)
                    ext = max(hw, hh)
                    for li, lid in enumerate(self.layer_ids):
                        if not pad.IsOnLayer(lid):
                            continue
                        self.rects[li].append((nc, (cx, cy, hw, hh, ang)))
                        if nc:
                            self._mark_own(li, cx - hw, cy, cx + hw, cy,
                                           ext + 0.3, nc)
                        # honest pad margin: extent + clearance + track
                        # half — must be >= CLEAR_MM(0.17) + w/2(0.1) =
                        # 0.27; 0.30 for the diagonal cell-center worst
                        # case (K14 V4: 0.25 was 0.02 short, 139
                        # orthogonal clearance violations on dev)
                        self._mark(li, cx - hw, cy, cx + hw, cy,
                                   ext + PAD_MARGIN,
                                   via_r=VIA_ANNULUS + 0.25 + ext)
                        if pad.GetAttribute() == _PTH:
                            try:
                                dr = (pad.GetDrillValue()
                                      or 500000) / MM / 2
                            except AttributeError:
                                dr = 0.5
                            self._mark_holes(cx, cy, dr + 0.125 + HOLE_KEEP)
                except AttributeError:
                    continue
        for it in self.board.GetTracks():
            try:
                nc = it.GetNetCode()
                if is_via(it):
                    q = it.GetPosition()
                    x, y = q.x / MM, q.y / MM
                    r = it.GetWidth(pcbnew.F_Cu) / MM / 2
                    self.own_via_cells.setdefault(
                        (int(x / GRID), int(y / GRID)), set()).add(nc)
                    for li in range(self.nl):
                        self.segs[li].append((nc, (x, y), (x, y), r))
                        if nc:
                            self._mark_own(li, x, y, x, y, r, nc)
                        self._mark(li, x, y, x, y, r + 0.1,
                                   via_r=r + 0.25 + 0.25)
                    self._mark_holes(x, y, r + 0.125 + HOLE_KEEP)
                else:
                    li = self.li_of(it.GetLayer())
                    if li < 0:
                        continue     # non-router layer (e.g. power plane)
                    s, e = it.GetStart(), it.GetEnd()
                    a = (s.x / MM, s.y / MM)
                    b = (e.x / MM, e.y / MM)
                    w = track_w(it)
                    self.segs[li].append((nc, a, b, w))
                    if nc:
                        n = max(2, int(((b[0] - a[0]) ** 2 +
                                        (b[1] - a[1]) ** 2) ** 0.5 / GRID))
                        for k in range(n + 1):
                            t = k / n
                            self._mark_own(li, a[0], a[1], b[0], b[1],
                                           max(w, 0.15), nc)
                    # Z3 CONSTRUCTIVE margin: a router track emitted
                    # between adjacent free cell centers must clear this
                    # copper by CLEAR_MM + half-widths in the worst case
                    # (copper endpoint perpendicular to the cell-center
                    # midpoint). w+0.15 admitted paths the exact check
                    # then vetoed — fc's dominant physical failure class.
                    self._mark(li, a[0], a[1], b[0], b[1],
                               w + TRACK_MARGIN)
            except AttributeError:
                continue

    # ------------------------------------------------- zone rasterization
    def _raster_zones(self):
        """KEEPOUT zones into occ (hard obstacles) — Z4 audit.

        Ordinary pours are deliberately NOT rasterized: KiCad zones are
        refillable and the filler retreats around router-placed copper
        (clearance carved on refill), so a GND pour on In1/In4 yields to
        crossing tracks — marking it blocked would destroy all F/B
        routing on every board with pours (dev/mini regression).
        Keepout rule areas never yield and must block.
        """
        try:
            zones = list(self.board.Zones())
        except TypeError:
            return
        for zone in zones:
            try:
                is_keep = bool(zone.IsKeepout())
            except (AttributeError, TypeError):
                try:
                    is_keep = "keepout" in str(
                        zone.GetZoneType()).lower()
                except (AttributeError, TypeError):
                    is_keep = False
            if not is_keep:
                continue
            try:
                lid = zone.GetLayer()
                li = self.li_of(lid)
                if li < 0:
                    continue
                outlines = _shape_outlines(zone.Outline())
                if not outlines:
                    bb = zone.GetBoundingBox()
                    x0, y0 = bb.GetX() / MM, bb.GetY() / MM
                    x1 = x0 + bb.GetWidth() / MM
                    y1 = y0 + bb.GetHeight() / MM
                    outlines = [[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]
                    self.zone_stats["bbox_fallback"] += 1
                self.zone_stats["zones"] += 1
                for pts in outlines:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    gx0 = max(0, int(min(xs) / GRID))
                    gx1 = min(self.nx, int(max(xs) / GRID) + 1)
                    gy0 = max(0, int(min(ys) / GRID))
                    gy1 = min(self.ny, int(max(ys) / GRID) + 1)
                    for gx in range(gx0, gx1):
                        cx = (gx + 0.5) * GRID
                        for gy in range(gy0, gy1):
                            cy = (gy + 0.5) * GRID
                            if _pip(cx, cy, pts):
                                self.occ[li][gy * self.nx + gx] = 1
                                self.zone_stats["cells"] += 1
            except (AttributeError, TypeError):
                continue

    # ------------------------------------------------- queries
    def fixed_free(self, li, gx, gy, nc):
        """Free of FIXED copper, or passable through own fixed copper."""
        i = self.idx(gx, gy)
        if i < 0:
            return False
        if self.occ[li][i] == 0:
            return True
        s = self.own_cells.get((li, gx, gy))
        return bool(s and nc in s)

    def via_cell_ok(self, li, gx, gy):
        i = self.idx(gx, gy)
        if i < 0:
            return False
        if self.hole_block[i]:
            return False
        return all(not self.via_block[k][i] for k in range(self.nl))

    def own_via_at(self, gx, gy, nc):
        s = self.own_via_cells.get((gx, gy))
        return bool(s and nc in s)

    # ------------------------------------------------- router occupancy
    def route_occupy(self, path_cells, nc):
        for c in path_cells:
            self.routed[c] = nc

    def route_release(self, path_cells):
        for c in path_cells:
            self.routed.pop(c, None)

    def overuse_cells(self):
        """Cells claimed by more than one net -> list of (cell, [nets]).

        A net 'claims' a cell if any of its paths contains it (multi-cell
        ownership per net is normal — the same net's two segments sharing
        a cell is NOT overuse)."""
        claims = {}
        for cell, nc in self.routed.items():
            claims.setdefault(cell, set()).add(nc)
        return [(c, ns) for c, ns in claims.items() if len(ns) > 1]

    # ------------------------------------------------- exact checks
    def clear_exact(self, x1, y1, x2, y2, lid, nc, w_self):
        li = self.li_of(lid)
        if li < 0:
            return False
        own = w_self / 2   # this track's half-width (was /2-0.1: allowed
                           # 0.27mm center distance vs 0.35 DRC requirement
                           # — the 139 clearance violations' root cause)
        for net, a, b, extra in self.segs[li]:
            if net == nc:
                continue
            if seg_seg_dist((x1, y1), (x2, y2), a, b) < CLEAR_MM + extra + own:
                return False
        import math
        for net, rect in self.rects[li]:
            if net == nc:
                continue
            n = max(2, int(math.hypot(x2 - x1, y2 - y1) / 0.05))
            for k in range(n + 1):
                t = k / n
                if pt_rect_dist(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t,
                                rect) < CLEAR_MM + w_self / 2:
                    return False
        return True

    def via_exact_ok(self, x, y, nc, va=None):
        va = VIA_ANNULUS if va is None else va
        for li in range(self.nl):
            for net, a, b, extra in self.segs[li]:
                if net == nc:
                    continue
                if seg_seg_dist((x, y), (x, y), a, b) < va + 0.15 + extra:
                    return False
            for net, rect in self.rects[li]:
                if net == nc:
                    continue
                if pt_rect_dist(x, y, rect) < va + 0.15:
                    return False
        if not (self.edge[0] + EDGE_KEEP <= x <= self.edge[2] - EDGE_KEEP and
                self.edge[1] + EDGE_KEEP <= y <= self.edge[3] - EDGE_KEEP):
            return False
        i = self.idx(int(x / GRID), int(y / GRID))
        return not self.hole_block[i] if i >= 0 else False


# ------------------------------------------------------------- board ops
def add_track(board, sx, sy, ex, ey, layer_id, net, w):
    if abs(ex - sx) < 0.01 and abs(ey - sy) < 0.01:
        return None
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(int(sx * MM), int(sy * MM)))
    t.SetEnd(pcbnew.VECTOR2I(int(ex * MM), int(ey * MM)))
    t.SetWidth(int(w * MM))
    t.SetLayer(layer_id)
    t.SetNet(net)
    board.Add(t)
    return t


def add_via(board, x, y, net, annulus=None, drill=None):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(int(x * MM), int(y * MM)))
    v.SetWidth(int((annulus or VIA_ANNULUS) * 2 * MM))
    v.SetDrill(int((drill or VIA_DRILL) * 2 * MM))
    v.SetNet(net)
    board.Add(v)
    return v


def pair_connected(board, pair):
    """Are this pair's anchor pads in ONE connectivity island? Immune to
    the +-1 global-count noise of zone refill web changes."""
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    nc = pair["nc"]

    def anchor_pad(x, y):
        best, bd = None, 1e9
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetCode() != nc:
                    continue
                q = pad.GetPosition()
                d = abs(q.x / MM - x) + abs(q.y / MM - y)
                if d < bd:
                    best, bd = pad, d
        return best

    pa = anchor_pad(*pair["a"][:2])
    pb = anchor_pad(*pair["b"][:2])
    if pa is None or pb is None:
        return False

    def key(item):
        try:
            q = item.GetPosition()
            return (q.x, q.y)
        except AttributeError:
            s = item.GetStart()
            return (s.x, s.y)

    seen = {key(pa)}
    stack = [pa]
    target = key(pb)
    while stack:
        cur = stack.pop()
        try:
            for nb in conn.GetConnectedItems(cur):
                if nb.GetNetCode() != nc:
                    continue
                k = key(nb)
                if k == target:
                    return True
                if k not in seen:
                    seen.add(k)
                    stack.append(k)
        except (AttributeError, TypeError):
            continue
    return False
