# -*- coding: utf-8 -*-
"""Single-process routing session — the whole converge loop inside ONE
KiCad Python process.

Replaces the 4-subprocess-per-iteration roundtrip chain (kicad-cli DRC
refill -> conn_audit -> route_embedded -> shorts_cleanup, each reloading
the board) whose zone-refill flapping made the ratsnest oscillate and whose
wall time reached ~90min/board under engine retries.

Flow (one LoadBoard, one Save):
  1. ZONE_FILLER(board).Fill(Zones())  — in-process copper refill (0.7s
     measured on h743-fc vs ~2min kicad-cli roundtrip)
  2. in-process audit: islands -> Prim-chained pairs (position-keyed
     union-find; SWIG wrapper ids are unstable so id()/m_Uuid cannot key)
  3. obstacle model built ONCE: exact seg/rect lists per layer + 0.2mm
     occupancy/via-block/hole grids
  4. power-plane stitching for fragmented power nets (two-layer checked)
  5. per-pair routing: cheap L attempt -> grid A* (shared grids, marks
     applied only after the in-process ratsnest CONFIRMS the closure —
     a failed attempt leaves no residue)
  6. bounded rip-up: a failed pair may evict the shortest session-placed
     track crossing its corridor (grids rebuilt per rip-up, <= MAX_RIPUP)
  7. re-audit + one more sweep for pairs opened by evictions

Prints one JSON line: closed/remaining/failed/ripups/time.
"""
import heapq
import json
import sys
import time

import pcbnew

PCB = sys.argv[1]
CLS = sys.argv[2] if len(sys.argv) > 2 else ""
MAX_RIPUP = int(sys.argv[3]) if len(sys.argv) > 3 else 12

T0 = time.time()
board = pcbnew.LoadBoard(PCB)
mm = 1000000

try:
    NET_CLASS = json.load(open(CLS)).get("classes", {})
except (OSError, ValueError):
    NET_CLASS = {}
WIDTHS = {"critical": 0.15, "normal": 0.2, "power": 0.4}
CLS_ORDER = {"critical": 0, "normal": 1, "power": 2}

# 0.16mm grid: divides the 0.8mm BGA pitch exactly (5 cells) so ball
# corridors are 2 cells wide (0.32/0.48 offsets clear the 0.3 pad margin)
# — with 0.2 they were single-lane and the diagonal corner rule strangled
# the A* on instances the flood fill proved reachable
GRID = 0.16
# edge clearance + small margin over the 0.15 DRC rule; 0.25 was
# over-strict and rejected legal diagonal escapes (20 seg-clear-fails
# per session on the h743-fc)
CLEAR_MM = 0.17
VIA_ANNULUS = 0.25           # via radius (0.5/0.25 via — BGA lattice
                              # pitch 0.8 needs <=0.75 via-via centers)
HOLE_KEEP = 0.28             # hole-to-hole edge spacing
EDGE_KEEP = 0.55

STATS = {"closed": 0, "failed": [], "ripups": 0, "session_tracks": [],
          "why": {}}


# ================================================================ geometry
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
    try:
        t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
        u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
        if 0 <= t <= 1 and 0 <= u <= 1:
            return 0.0
    except ZeroDivisionError:
        pass
    return min(pt_seg_dist(x1, y1, x3, y3, x4, y4),
               pt_seg_dist(x2, y2, x3, y3, x4, y4),
               pt_seg_dist(x3, y3, x1, y1, x2, y2),
               pt_seg_dist(x4, y4, x1, y1, x2, y2))


def is_via(item):
    return item.Type() == pcbnew.PCB_VIA_T


_PTH = getattr(pcbnew, "PAD_ATTRIB_PTH",
               getattr(pcbnew, "PAD_ATTRIB_THROUGH_HOLE", 3))


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


def track_w(net_name):
    return WIDTHS.get(NET_CLASS.get(net_name, "normal"), 0.2)


_bb = board.GetBoardEdgesBoundingBox()
EDGE = (_bb.GetX() / mm, _bb.GetY() / mm,
        (_bb.GetX() + _bb.GetWidth()) / mm,
        (_bb.GetY() + _bb.GetHeight()) / mm)


def edge_ok(*pts):
    for x, y in pts:
        if not (EDGE[0] + EDGE_KEEP <= x <= EDGE[2] - EDGE_KEEP and
                EDGE[1] + EDGE_KEEP <= y <= EDGE[3] - EDGE_KEEP):
            return False
    return True


# ================================================================ commit
def add_track(sx, sy, ex, ey, layer_id, net, w=None):
    if abs(ex - sx) < 0.01 and abs(ey - sy) < 0.01:
        return None
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(int(sx * mm), int(sy * mm)))
    t.SetEnd(pcbnew.VECTOR2I(int(ex * mm), int(ey * mm)))
    t.SetWidth(int((w or track_w(net.GetNetname() or "")) * mm))
    t.SetLayer(layer_id)
    t.SetNet(net)
    board.Add(t)
    return t


def add_via(x, y, net, annulus=0.25, drill=0.125):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(int(x * mm), int(y * mm)))
    v.SetWidth(int(annulus * 2 * mm))
    v.SetDrill(int(drill * 2 * mm))
    v.SetNet(net)
    board.Add(v)
    return v


# ================================================================ zone fill
def fill_zones():
    try:
        f = pcbnew.ZONE_FILLER(board)
        return bool(f.Fill(board.Zones()))
    except Exception:
        return False


def ratsnest_count():
    board.BuildConnectivity()
    return board.GetConnectivity().GetUnconnectedCount(True)


_VERIFY_FILL_EVERY = 1     # refill zones per-N verifications (accuracy vs 0.7s)
_verify_i = [0]


def verify_count():
    """Ratsnest count accurate w.r.t. zone fills: a freshly placed track can
    SPLIT a zone region and disconnect a pad that the stale fill still
    shows connected — recount only after a refill."""
    _verify_i[0] += 1
    if _VERIFY_FILL_EVERY and _verify_i[0] % _VERIFY_FILL_EVERY == 0:
        fill_zones()
    return ratsnest_count()


# ================================================================ audit
def _anchors_of(item):
    t = type(item).__name__
    out = []
    if t == "PAD":
        p = item.GetPosition()
        out.append((p.x, p.y))
    elif t == "PCB_VIA":
        p = item.GetPosition()
        out.append((p.x, p.y))
    elif t == "PCB_TRACK":
        s, e = item.GetStart(), item.GetEnd()
        out.append((s.x, s.y))
        out.append((e.x, e.y))
    return out


class _UF:
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


def audit_pairs():
    """(pairs, count, fragmented) — pairs as dicts with anchors/layers."""
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    count = conn.GetUnconnectedCount(True)
    pads_by_net = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if nc:
                pads_by_net.setdefault(nc, []).append(pad)

    def pad_anchor(pad):
        lay = 1 if (pad.IsOnLayer(pcbnew.B_Cu) and
                    not pad.IsOnLayer(pcbnew.F_Cu)) else 0
        p = pad.GetPosition()
        return (p.x / mm, p.y / mm, lay)

    pairs = []
    fragmented = {}
    for nc, pads in pads_by_net.items():
        if nc == 0 or len(pads) < 2:
            continue
        uf = _UF()
        keys = [_anchors_of(p)[0] for p in pads]
        for k in keys:
            uf.find(k)
        for pad, k in zip(pads, keys):
            try:
                for nb in conn.GetConnectedItems(pad):
                    if nb.GetNetCode() != nc:
                        continue
                    for a in _anchors_of(nb):
                        uf.union(k, a)
            except (AttributeError, TypeError):
                pass
        islands = {}
        for pad, k in zip(pads, keys):
            islands.setdefault(uf.find(k), []).append(pad)
        if len(islands) < 2:
            continue
        name = pads[0].GetNetname()
        fragmented[name] = len(islands)
        anchors = [[pad_anchor(p) for p in isl] for isl in islands.values()]
        inside = {0}
        while len(inside) < len(islands):
            best = None
            for ia in inside:
                for ib in range(len(islands)):
                    if ib in inside:
                        continue
                    for pa in anchors[ia]:
                        for pb in anchors[ib]:
                            d2 = (pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2
                            if best is None or d2 < best[0]:
                                best = (d2, ia, ib, pa, pb)
            _, ia, ib, pa, pb = best
            pairs.append({"net": name, "nc": nc,
                          "a": pa, "b": pb,
                          "d": ((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5})
            inside.add(ib)
    return pairs, count, fragmented


# ================================================================ obstacles
class Obstacles:
    """Exact per-layer seg/rect lists + coarse grids, built once per sweep.

    occ: 0 free / 1 blocked / 255 own-net-passable (set per pair query-time
    via own_net marks — see mark_own/unmark_own bookkeeping below).
    In practice own-copper is rare enough to track with a dict of cells."""

    def __init__(self):
        self.nx = int((EDGE[2] - EDGE[0]) / GRID) + 2
        self.ny = int((EDGE[3] - EDGE[1]) / GRID) + 2
        self.occ = [bytearray(self.nx * self.ny) for _ in range(2)]
        self.via_block = [bytearray(self.nx * self.ny) for _ in range(2)]
        self.hole_block = bytearray(self.nx * self.ny)
        self.own_cells = {}          # (lay, gx, gy) -> set(netcodes)
        self.own_via_cells = {}      # (gx, gy) -> set(netcodes)
        self.segs = ([], [])         # per layer: (netcode, a, b, extra)
        self.rects = ([], [])        # per layer: (netcode, rect)
        self._rasterize_board()

    # ---- board state -> model
    def _rasterize_board(self):
        for item in board.GetTracks():
            self._add_item(item)
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                try:
                    nc = pad.GetNetCode()
                    cx, cy, hw, hh, ang = pad_rect(pad)
                    for li, lid in ((0, pcbnew.F_Cu), (1, pcbnew.B_Cu)):
                        if not pad.IsOnLayer(lid):
                            continue
                        self.rects[li].append((nc, (cx, cy, hw, hh, ang)))
                        # extent + clearance + track half: inter-ball gaps at
                        # 0.8mm pitch admit NO track (0.566-0.2-0.2-0.1<0.15),
                        # so the grid must not offer them — escape forces
                        # via-in-pad, the only physical route out
                        self._mark(li, cx - hw, cy, cx + hw, cy,
                                   max(hw, hh) + 0.25,
                                   via_r=VIA_ANNULUS + 0.25 + max(hw, hh))
                        if pad.GetAttribute() == _PTH:
                            try:
                                dr = (pad.GetDrillValue() or 500000) / mm / 2
                            except AttributeError:
                                dr = 0.5
                            self._mark_holes(cx, cy,
                                             dr + 0.125 + HOLE_KEEP)
                except AttributeError:
                    continue

    def _add_item(self, item):
        try:
            nc = item.GetNetCode()
            if is_via(item):
                p = item.GetPosition()
                x, y = p.x / mm, p.y / mm
                r = item.GetWidth(pcbnew.F_Cu) / mm / 2
                self.own_via_cells.setdefault(
                    (int(x / GRID), int(y / GRID)), set()).add(nc)
                for li in (0, 1):
                    self.segs[li].append((nc, (x, y), (x, y), r))
                    self._mark(li, x, y, x, y, r + 0.1,
                               via_r=r + 0.25 + 0.25)
                self._mark_holes(x, y, r + 0.125 + HOLE_KEEP)
                return
            lid = item.GetLayer()
            li = 0 if lid == pcbnew.F_Cu else 1
            s, e = item.GetStart(), item.GetEnd()
            a, b = (s.x / mm, s.y / mm), (e.x / mm, e.y / mm)
            try:
                w = item.GetWidth(pcbnew.F_Cu) / mm / 2
            except TypeError:
                w = item.GetWidth() / mm / 2
            self.segs[li].append((nc, a, b, w))
            self._mark(li, a[0], a[1], b[0], b[1], w + 0.15)
        except AttributeError:
            pass

    def add_placed(self, items):
        """Register committed session items: obstacles + own-copper goals."""
        for it in items:
            self._add_item(it)
            try:
                nc = it.GetNetCode()
                if is_via(it):
                    q = it.GetPosition()
                    r = it.GetWidth(pcbnew.F_Cu) / mm / 2
                    for li in (0, 1):
                        self.mark_own(li, q.x / mm, q.y / mm, q.x / mm,
                                      q.y / mm, r, nc)
                else:
                    li = 0 if it.GetLayer() == pcbnew.F_Cu else 1
                    s, e = it.GetStart(), it.GetEnd()
                    self.mark_own(li, s.x / mm, s.y / mm, e.x / mm, e.y / mm,
                                  0.15, nc)
            except AttributeError:
                pass

    # ---- marking
    def _idx(self, gx, gy):
        if gx < 1 or gy < 1 or gx >= self.nx - 1 or gy >= self.ny - 1:
            return -1
        return gy * self.nx + gx

    def _mark(self, li, x0, y0, x1, y1, margin, via_r=None):
        gx0 = int((min(x0, x1) - margin - 0.15) / GRID)
        gx1 = int((max(x0, x1) + margin + 0.15) / GRID) + 1
        gy0 = int((min(y0, y1) - margin - 0.15) / GRID)
        gy1 = int((max(y0, y1) + margin + 0.15) / GRID) + 1
        m2 = max(margin, via_r or 0)
        bx0 = int((min(x0, x1) - m2) / GRID)
        bx1 = int((max(x0, x1) + m2) / GRID) + 1
        by0 = int((min(y0, y1) - m2) / GRID)
        by1 = int((max(y0, y1) + m2) / GRID) + 1
        for gx in range(max(0, bx0), min(self.nx, bx1)):
            cx = (gx + 0.5) * GRID
            for gy in range(max(0, by0), min(self.ny, by1)):
                cy = (gy + 0.5) * GRID
                d = pt_seg_dist(cx, cy, x0, y0, x1, y1)
                i = gy * self.nx + gx
                if d <= margin:
                    self.occ[li][i] = 1
                if via_r and d <= via_r:
                    self.via_block[li][i] = 1

    def _mark_holes(self, x, y, r):
        gx0, gx1 = int((x - r) / GRID), int((x + r) / GRID) + 1
        gy0, gy1 = int((y - r) / GRID), int((y + r) / GRID) + 1
        for gx in range(max(0, gx0), min(self.nx, gx1)):
            for gy in range(max(0, gy0), min(self.ny, gy1)):
                cx, cy = (gx + 0.5) * GRID, (gy + 0.5) * GRID
                if (cx - x) ** 2 + (cy - y) ** 2 <= r * r:
                    self.hole_block[gy * self.nx + gx] = 1

    def mark_own(self, li, x0, y0, x1, y1, half_w, nc):
        """Register same-net copper cells (A* goals + passable through)."""
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

    # ---- queries
    def free(self, li, gx, gy, nc):
        i = self._idx(gx, gy)
        if i < 0:
            return False
        if self.occ[li][i] == 0:
            return True
        # blocked by copper — passable only through THIS net's own copper
        s = self.own_cells.get((li, gx, gy))
        return bool(s and nc in s)

    def cell_is_own(self, li, gx, gy, nc):
        s = self.own_cells.get((li, gx, gy))
        return bool(s and nc in s)

    def own_via_at(self, gx, gy, nc):
        """An existing same-net via bridges the layers at this cell — the
        A* crosses for free instead of placing a duplicate via."""
        s = self.own_via_cells.get((gx, gy))
        return bool(s and nc in s)

    def via_cell_ok(self, li, gx, gy):
        i = self._idx(gx, gy)
        return (i >= 0 and not self.via_block[li][i]
                and not self.via_block[1 - li][i] and not self.hole_block[i])

    def clear_exact(self, x1, y1, x2, y2, lid, nc, w_self):
        """Exact segment clearance vs other-net copper on one layer."""
        li = 0 if lid == pcbnew.F_Cu else 1
        own = max(0.0, w_self / 2 - 0.1)
        for net, a, b, extra in self.segs[li]:
            if net == nc:
                continue
            if seg_seg_dist((x1, y1), (x2, y2), a, b) < CLEAR_MM + extra + own:
                return False
        for net, rect in self.rects[li]:
            if net == nc:
                continue
            # rect check via sampling (short stubs only); center-to-edge
            # must clear by clearance + the NEW track's half width
            import math
            n = max(2, int(math.hypot(x2 - x1, y2 - y1) / 0.05))
            for k in range(n + 1):
                t = k / n
                if pt_rect_dist(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t,
                                rect) < CLEAR_MM + w_self / 2:
                    return False
        return True

    def via_exact_ok(self, x, y, nc, va=None):
        va = VIA_ANNULUS if va is None else va
        for li in (0, 1):
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
        if not edge_ok((x, y)):
            return False
        return not self.hole_block[self._idx(int(x / GRID), int(y / GRID))] \
            if self._idx(int(x / GRID), int(y / GRID)) >= 0 else False


# ================================================================ A*
def to_cell(ob, x, y, li, nc):
    gx, gy = int(x / GRID), int(y / GRID)
    for r in range(0, 6):
        for dx in range(-r, r + 1):
            for dy in (range(-r, r + 1) if abs(dx) == r else (0,)):
                if ob.free(li, gx + dx, gy + dy, nc):
                    return gx + dx, gy + dy
    return None


def astar(ob, nc, a, b, w_self, no_diag=False):
    """Grid A* a->b with via moves. Own-net copper near b are goals.
    Returns list of committed board items, or None."""
    x1, y1, l1 = a
    x2, y2, l2 = b
    # own-net copper near BOTH anchors is passable + goal-worthy
    # anchor pads: start/goal = THE PADS' own cells, never other net
    # copper nearby — dangling same-net track fragments inside the old
    # 1.2mm goal radius made the A* "arrive" at copper outside the target
    # island (every verify-fail in the logs)
    def anchor_pad_cells(x, y, nc):
        best, bd = None, 1e9
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetCode() != nc:
                    continue
                q = pad.GetPosition()
                d = abs(q.x / mm - x) + abs(q.y / mm - y)
                if d < bd:
                    best, bd = pad, d
        if best is None:
            return None, None, None
        cx, cy, hw, hh, ang = pad_rect(best)
        r = max(hw, hh) + 0.1
        return cx, cy, r

    scx, scy, sr = anchor_pad_cells(x1, y1, nc)
    if scx is None:
        return None
    start = None
    for gx in range(int((scx - sr) / GRID), int((scx + sr) / GRID) + 1):
        for gy in range(int((scy - sr) / GRID), int((scy + sr) / GRID) + 1):
            if ob.free(l1, gx, gy, nc):
                start = (gx, gy)
                break
        if start:
            break
    if start is None:
        start = to_cell(ob, x1, y1, l1, nc)
    if start is None:
        return None

    tcx, tcy, tr = anchor_pad_cells(x2, y2, nc)
    goals = set()
    if tcx is not None:
        for li in (0, 1):
            for gx in range(int((tcx - tr) / GRID), int((tcx + tr) / GRID) + 1):
                for gy in range(int((tcy - tr) / GRID), int((tcy + tr) / GRID) + 1):
                    if ob.cell_is_own(li, gx, gy, nc):
                        goals.add((li, gx, gy))
    if not goals:
        c = to_cell(ob, x2, y2, l2, nc)
        if c:
            goals.add((l2, c[0], c[1]))
    if not goals:
        return None
    sx, sy = start
    heap = [(0.0, 0, (l1, sx, sy))]
    came, gscore = {}, {(l1, sx, sy): 0.0}
    # cheap vias: the BGA field's F.Cu corridors jam quickly; via-in-pad
    # (0.5/0.25 fits the 0.8 lattice) down to the wide-open B.Cu is the
    # standard escape and the A* should prefer it over threading lanes
    VIA_COST = 8.0
    count = 0
    found = None
    while heap:
        _, _, node = heapq.heappop(heap)
        if node in goals:
            found = node
            break
        lay, gx, gy = node
        cur = gscore[node]
        moves = ((1, 0), (-1, 0), (0, 1), (0, -1)) if no_diag else (
            (1, 0), (-1, 0), (0, 1), (0, -1),
             (1, 1), (1, -1), (-1, 1), (-1, -1))
        for dx, dy in moves:
            gx2, gy2 = gx + dx, gy + dy
            if not ob.free(lay, gx2, gy2, nc):
                continue
            if dx and dy and not (ob.free(lay, gx + dx, gy, nc)
                                  or ob.free(lay, gx, gy + dy, nc)):
                # half-rule: inter-ball gaps (0.8 pitch) admit NO straight
                # track (need 0.85), so BGA escape is diagonal-only and both
                # orthogonals are legitimately blocked — the strict corner
                # rule made escape impossible. Clip safety moves to the
                # exact per-segment verification at commit time.
                continue
            step = GRID * (1.4142 if dx and dy else 1.0)
            nk = (lay, gx2, gy2)
            if gscore.get(nk, 1e18) > cur + step:
                gscore[nk] = cur + step
                came[nk] = node
                count += 1
                hx = abs(gx2 - int((tcx or x2) / GRID)) + abs(gy2 - int((tcy or y2) / GRID))
                # weighted A* (W=2): far fewer expansions for slightly
                # suboptimal paths — the plain form burned the whole node
                # budget on goals the flood fill proved reachable
                heapq.heappush(heap, (cur + step + 2.0 * hx * GRID, count, nk))
        nk = (1 - lay, gx, gy)
        bridge = ob.own_via_at(gx, gy, nc)
        if (ob.free(1 - lay, gx, gy, nc)
                and (bridge or ob.via_cell_ok(1 - lay, gx, gy))
                and gscore.get(nk, 1e18) > cur + VIA_COST):
            gscore[nk] = cur + VIA_COST
            came[nk] = node
            count += 1
            heapq.heappush(heap, (cur + VIA_COST, count, nk))
        if count > 800000:
            _why("no-path-budget")
            return None
    if not found:
        _why("no-path-unreachable")
        return None

    # path -> board items (stubs + collinear-merged segments + vias)
    chain = []
    node = found
    while node and node != (l1, sx, sy):
        chain.append(node)
        node = came.get(node)
    chain.append((l1, sx, sy))
    chain.reverse()
    net = board.FindNet(_CURRENT["net"])
    items = []
    lid = lambda li: pcbnew.F_Cu if li == 0 else pcbnew.B_Cu
    fx, fy = (chain[0][1] + 0.5) * GRID, (chain[0][2] + 0.5) * GRID
    lx, ly = (chain[-1][1] + 0.5) * GRID, (chain[-1][2] + 0.5) * GRID
    end_lay = chain[-1][0]
    if not ob.clear_exact(x1, y1, fx, fy, lid(l1), nc, w_self):
        return None
    if not ob.clear_exact(lx, ly, x2, y2, lid(end_lay), nc, w_self):
        return None
    t = add_track(x1, y1, fx, fy, lid(l1), net)
    if t:
        items.append(t)
    t = add_track(lx, ly, x2, y2, lid(end_lay), net)
    if t:
        items.append(t)
    pts = [(li, (gx + 0.5) * GRID, (gy + 0.5) * GRID) for li, gx, gy in chain]
    # collect pending segments, verify each EXACTLY, abort the whole path
    # on any violation (diagonal corner-cutting is allowed by the half-rule;
    # this is the geometric safety net)
    # per-step emission: one track per adjacent cell pair (NO collinear
    # merging — the merge produced one-cell gaps between supposedly
    # contiguous segments and every such gap broke the electrical chain)
    pending = []
    for k in range(len(pts) - 1):
        li, px, py = pts[k]
        li2, qx, qy = pts[k + 1]
        if li2 != li:
            # layer switch NEEDS a via (or reuses an existing same-net one)
            if not ob.own_via_at(int(px / GRID), int(py / GRID), nc):
                if not ob.via_exact_ok(px, py, nc):
                    for it in items:
                        board.Remove(it)
                    return None
                items.append(add_via(px, py, net))
            continue
        pending.append((li, px, py, qx, qy))
    for li, px, py, ex, ey in pending:
        # only diagonal steps can clip corners; axis steps are grid-safe
        if abs(ex - px) >= 0.01 and abs(ey - py) >= 0.01:
            if not ob.clear_exact(px, py, ex, ey,
                                  pcbnew.F_Cu if li == 0 else pcbnew.B_Cu,
                                  nc, w_self):
                for it in items:
                    board.Remove(it)
                _why("seg-clear-fail")
                return None
        t = add_track(px, py, ex, ey,
                      pcbnew.F_Cu if li == 0 else pcbnew.B_Cu, net)
        if t:
            items.append(t)
    return [i for i in items if i is not None]


_CURRENT = {"net": ""}


def _why(key):
    STATS["why"][key] = STATS["why"].get(key, 0) + 1


def pair_connected(p):
    """Are this pair's two anchor pads in ONE connectivity island now?
    Immune to the +-1 global-count noise from zone refill web changes
    (a routed pair closing while a GND web flap adds +1 elsewhere reads
    as 'no improvement' to the old count-drop check)."""
    board.BuildConnectivity()
    conn = board.GetConnectivity()

    def anchor_pad(x, y, nc):
        best, bd = None, 1e9
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetCode() != nc:
                    continue
                q = pad.GetPosition()
                d = abs(q.x / mm - x) + abs(q.y / mm - y)
                if d < bd:
                    best, bd = pad, d
        return best

    pa = anchor_pad(*p["a"][:2], p["nc"])
    pb = anchor_pad(*p["b"][:2], p["nc"])
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
                if nb.GetNetCode() != p["nc"]:
                    continue
                k = key(nb)
                if k == target:
                    return True
                if k not in seen:
                    seen.add(k)
                    stack.append(nb)
        except (AttributeError, TypeError):
            continue
    return False


def try_route_pair(ob, pair, no_diag=False):
    """Route one pair: L attempt, then A*. Commit only when the in-process
    ratsnest actually drops (verified instantly), else roll back cleanly."""
    nc = pair["nc"]
    net_name = pair["net"]
    _CURRENT["net"] = net_name
    net = board.FindNet(net_name)
    if not net:
        _why("no-net")
        return False
    w = track_w(net_name)
    before = verify_count()
    x1, y1, l1 = pair["a"]
    x2, y2, l2 = pair["b"]
    # cheap L attempt (both orders) on each involved layer
    attempts = []
    for li in {l1, l2}:
        lid = pcbnew.F_Cu if li == 0 else pcbnew.B_Cu
        attempts.append((li, x2, y1, (x1, y1), (x2, y2)))
        attempts.append((li, x1, y2, (x1, y1), (x2, y2)))
    l_tried = 0
    for li, mx, my, pa, pb in attempts:
        lid = pcbnew.F_Cu if li == 0 else pcbnew.B_Cu
        if not edge_ok(pa, pb, (mx, my)):
            continue
        l_tried += 1
        need1, need2 = li != l1, li != l2
        items = []
        t = add_track(x1, y1, mx, my, lid, net)
        if t:
            items.append(t)
        t = add_track(mx, my, x2, y2, lid, net)
        if t:
            items.append(t)
        if need1:
            if ob.via_exact_ok(x1, y1, nc):
                items.append(add_via(x1, y1, net))
            else:
                for it in items:
                    board.Remove(it)
                continue
        if need2:
            if ob.via_exact_ok(x2, y2, nc):
                items.append(add_via(x2, y2, net))
            else:
                for it in items:
                    board.Remove(it)
                continue
        # exact clearance on both legs
        ok = (ob.clear_exact(x1, y1, mx, my, lid, nc, w)
              and ob.clear_exact(mx, my, x2, y2, lid, nc, w))
        if not ok:
            for it in items:
                board.Remove(it)
            continue
        if pair_connected(pair):
            ob.add_placed(items)
            STATS["session_tracks"].extend(items)
            return True
        for it in items:
            board.Remove(it)
    # A* fallback
    import os as _os
    _nd = no_diag or _os.environ.get("FLUXNT_ORTHO") == "1"
    path = astar(ob, nc, pair["a"], pair["b"], w, no_diag=_nd)
    if path:
        if pair_connected(pair):
            ob.add_placed(path)
            STATS["session_tracks"].extend(path)
            return True
        for it in path:
            board.Remove(it)
        _why("verify-fail")
        return False
    return False


# ================================================================ stitching
def power_stitch(ob, pairs, boxes=()):
    """Vias tying fragmented power pads to their inner planes. Two-layer
    exact-checked; committed only when the ratsnest drops.

    BGA power balls use VIA-IN-PAD ONLY: the old ±0.6mm offset candidates
    land exactly on the diagonal-center lattice (0.6*sqrt(2) ~= the 0.566
    dog-bone ring) and squat the escape centers of neighbouring signal
    balls — a sealed ball stays sealed."""
    done = 0
    for p in pairs:
        if NET_CLASS.get(p["net"]) != "power":
            continue
        net = board.FindNet(p["net"])
        nc = p["nc"]
        for x, y, lay in (p["a"], p["b"]):
            before = verify_count()
            placed = False
            in_bga = any(x0 <= x <= x1 and y0 <= y <= y1
                         for (x0, y0, x1, y1) in boxes)
            cands = ((0, 0),) if in_bga else                 ((0, 0), (0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6))
            for dx, dy in cands:
                vx, vy = x + dx, y + dy
                if not edge_ok((vx, vy)):
                    continue
                # skip THT pads (span layers already) — cheap probe
                tht = False
                for fp in board.GetFootprints():
                    for pad in fp.Pads():
                        if pad.GetNetCode() != nc:
                            continue
                        if pad.GetAttribute() != _PTH:
                            continue
                        pp = pad.GetPosition()
                        if abs(pp.x / mm - x) < 0.4 and abs(pp.y / mm - y) < 0.4:
                            tht = True
                    if tht:
                        break
                if tht:
                    continue
                if not ob.via_exact_ok(vx, vy, nc):
                    continue
                items = [add_via(vx, vy, net)]
                if dx or dy:
                    t = add_track(x, y, vx, vy, pcbnew.F_Cu, net)
                    if not ob.clear_exact(x, y, vx, vy, pcbnew.F_Cu, nc,
                                          track_w(p["net"])):
                        board.Remove(items[0])
                        if t:
                            board.Remove(t)
                        continue
                    if t:
                        items.append(t)
                if verify_count() < before:
                    ob.add_placed(items)
                    STATS["session_tracks"].extend(items)
                    done += 1
                    placed = True
                    break
                for it in items:
                    board.Remove(it)
            if placed:
                break
    return done


# ================================================================ main
def sweep(ob, pairs):
    pairs.sort(key=lambda p: (CLS_ORDER.get(NET_CLASS.get(p["net"], "normal"), 1), p["d"]))
    closed = 0
    for p in pairs:
        if try_route_pair(ob, p):
            closed += 1
        else:
            STATS["failed"].append(p)
    return closed


def mark_own_copper(ob):
    """Own-net copper anchors (A* goals) for every net, keyed by netcode."""
    for item in board.GetTracks():
        try:
            nc = item.GetNetCode()
            if is_via(item):
                p = item.GetPosition()
                for li in (0, 1):
                    ob.mark_own(li, p.x / mm, p.y / mm, p.x / mm, p.y / mm,
                                0.3, nc)
            else:
                li = 0 if item.GetLayer() == pcbnew.F_Cu else 1
                s, e = item.GetStart(), item.GetEnd()
                ob.mark_own(li, s.x / mm, s.y / mm, e.x / mm, e.y / mm,
                            0.15, nc)
        except AttributeError:
            pass
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                nc = pad.GetNetCode()
                cx, cy, hw, hh, ang = pad_rect(pad)
                for li, lid in ((0, pcbnew.F_Cu), (1, pcbnew.B_Cu)):
                    if pad.IsOnLayer(lid):
                        ob.mark_own(li, cx - hw, cy, cx + hw, cy,
                                    max(hw, hh) + 0.3, nc)
            except AttributeError:
                pass


def bga_boxes():
    """BGA footprints' bboxes (SMD pad count >= 60). MUST run before any
    zone fill / connectivity churn — fp.Pads() iteration started raising
    SwigPyObject-not-iterable after ZONE_FILLER + GetConnectedItems."""
    boxes = []
    for fp in board.GetFootprints():
        try:
            pads = [p for p in fp.Pads()
                    if p.GetAttribute() != _PTH and p.IsOnLayer(pcbnew.F_Cu)]
        except TypeError:
            continue
        if len(pads) < 60:
            continue
        bb = fp.GetBoundingBox()
        boxes.append(((bb.GetX() - 500000) / mm, (bb.GetY() - 500000) / mm,
                      (bb.GetX() + bb.GetWidth() + 500000) / mm,
                      (bb.GetY() + bb.GetHeight() + 500000) / mm))
    return boxes


def clear_bga_bcup(boxes):
    """Remove B.Cu TRACKS under BGA footprints — Freerouting routed them
    there and they block the dog-bone via landings (the via pad itself is
    fine: same net or anti-padded; a foreign track under the center kills
    via_exact_ok). Vias are kept; nets whose escapes ran on B.Cu re-open
    and the sweep re-routes them on the freed copper."""
    removed = 0
    for it in list(board.GetTracks()):
        if is_via(it) or it.GetLayer() != pcbnew.B_Cu:
            continue
        s, e = it.GetStart(), it.GetEnd()
        mx, my = (s.x + e.x) / 2 / mm, (s.y + e.y) / 2 / mm
        for (x0, y0, x1, y1) in boxes:
            if x0 <= mx <= x1 and y0 <= my <= y1:
                board.Remove(it)
                removed += 1
                break
    return removed


def dogbone_fanout(ob, pairs):
    """Industry-standard BGA escape (Altium/AMD UG1099 for >=0.8mm pitch):
    a small via at the CENTER OF FOUR BALLS plus a short diagonal track
    from the ball pad. Inter-ball gaps at 0.8mm pitch admit no track —
    every signal ball needs this fanout. With 0.3mm UG1099 balls and a
    0.4/0.2 dog-bone via at the 0.566mm center: via-edge to ball-edge
    0.566-0.15-0.1 = 0.316mm (>=0.15 OK); adjacent dog-bones 0.8mm apart
    (hole-to-hole 0.6 >= 0.25 OK)."""
    open_nets = {p["nc"] for p in pairs}
    placed = 0
    used_centers = set()
    for fp in board.GetFootprints():
        pads = [p for p in fp.Pads()
                if p.GetAttribute() != _PTH and p.IsOnLayer(pcbnew.F_Cu)]
        if len(pads) < 60:
            continue                      # BGA-sized footprints only
        # pitch from nearest-neighbour pad distance
        pts = [(p.GetPosition().x / mm, p.GetPosition().y / mm)
               for p in pads]
        pitch = 0.8
        if len(pts) > 1:
            ds = []
            for i, (x1, y1) in enumerate(pts[:80]):
                n = min(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5
                        for j, (x2, y2) in enumerate(pts) if j != i)
                ds.append(n)
            pitch = sorted(ds)[len(ds) // 2]
        half = pitch / 2.0
        # outer balls first (escape lanes run outward to the field edge)
        fx, fy = fp.GetPosition().x / mm, fp.GetPosition().y / mm
        pads.sort(key=lambda p: -abs(p.GetPosition().x / mm - fx)
                  - abs(p.GetPosition().y / mm - fy))
        for pad in pads:
            nc = pad.GetNetCode()
            if nc == 0 or nc not in open_nets:
                continue
            if NET_CLASS.get(pad.GetNetname() or "") == "power":
                continue                  # power balls: plane stitching
            p = pad.GetPosition()
            x, y = p.x / mm, p.y / mm
            net = board.FindNet(pad.GetNetname())
            if not net:
                continue
            # prefer the diagonal toward the board center (outward escape)
            cx0, cy0 = (EDGE[0] + EDGE[2]) / 2, (EDGE[1] + EDGE[3]) / 2
            offs = sorted(((half, half), (-half, half), (half, -half),
                           (-half, -half)),
                          key=lambda o: -((o[0] * (cx0 - x) +
                                           o[1] * (cy0 - y))))
            for dx, dy in offs:
                vx, vy = x + dx, y + dy
                key = (round(vx, 2), round(vy, 2))
                if key in used_centers:
                    continue        # each 4-ball center hosts ONE dog-bone
                if not edge_ok((vx, vy)):
                    continue
                if not ob.via_exact_ok(vx, vy, nc, va=0.2):
                    continue
                if not ob.clear_exact(x, y, vx, vy, pcbnew.F_Cu, nc, 0.15):
                    continue
                t = add_track(x, y, vx, vy, pcbnew.F_Cu, net, w=0.15)
                v = add_via(vx, vy, net, annulus=0.2, drill=0.1)
                if t or v:
                    ob.add_placed([it for it in (t, v) if it])
                    used_centers.add(key)
                    placed += 1
                break
    return placed


def module_fanout(ob, pairs):
    """Perpendicular stub+via fanout for SMD pad ROWS (castellated module
    pads etc.): every pad gets a 1.0mm perpendicular escape with a small
    via BEFORE general routing — same principle as the BGA dog-bone; the
    row's exits are too tight to leave to the sweep (each unrouted pad got
    sealed by its neighbours' escapes)."""
    open_nets = {p["nc"] for p in pairs}
    placed = 0
    for fp in board.GetFootprints():
        try:
            pads = [p for p in fp.Pads()
                    if p.GetAttribute() != _PTH and p.IsOnLayer(pcbnew.F_Cu)]
        except TypeError:
            continue
        if not (8 <= len(pads) < 60):
            continue
        # detect a single row: pads share ~one y (or x) within tolerance
        ys = [p.GetPosition().y / mm for p in pads]
        xs = [p.GetPosition().x / mm for p in pads]
        span_y, span_x = max(ys) - min(ys), max(xs) - min(xs)
        horizontal_row = span_x > span_y * 3
        vertical_row = span_y > span_x * 3
        if not (horizontal_row or vertical_row):
            continue
        # escape direction: toward the board center
        cx0, cy0 = (EDGE[0] + EDGE[2]) / 2, (EDGE[1] + EDGE[3]) / 2
        for pad in pads:
            nc = pad.GetNetCode()
            if nc == 0 or nc not in open_nets:
                continue
            name = pad.GetNetname()
            if NET_CLASS.get(name) == "power":
                continue
            q = pad.GetPosition()
            x, y = q.x / mm, q.y / mm
            net = board.FindNet(name)
            if not net:
                continue
            if horizontal_row:
                dy = 1.0 if cy0 > y else -1.0
                cands = ((x, y + dy), (x, y - dy))
            else:
                dx = 1.0 if cx0 > x else -1.0
                cands = ((x + dx, y), (x - dx, y))
            for vx, vy in cands:
                if not edge_ok((vx, vy)):
                    continue
                if not ob.via_exact_ok(vx, vy, nc, va=0.2):
                    continue
                if not ob.clear_exact(x, y, vx, vy, pcbnew.F_Cu, nc, 0.15):
                    continue
                tk = add_track(x, y, vx, vy, pcbnew.F_Cu, net, w=0.15)
                v = add_via(vx, vy, net, annulus=0.2, drill=0.1)
                if tk or v:
                    ob.add_placed([it for it in (tk, v) if it])
                    placed += 1
                break
    return placed


def main():
    boxes = bga_boxes()
    filled = fill_zones()
    pairs, count0, frag = audit_pairs()
    ob = Obstacles()
    mark_own_copper(ob)
    stitched = power_stitch(ob, pairs, boxes)
    modfan = module_fanout(ob, pairs)
    dogboned = dogbone_fanout(ob, pairs)
    count1 = ratsnest_count()
    closed = sweep(ob, pairs)
    count2 = ratsnest_count()

    # bounded rip-up for the still-failed pairs
    rip = 0
    failed = [p for p in STATS["failed"]]
    failed.sort(key=lambda p: p["d"])
    for p in failed:
        if rip >= MAX_RIPUP or count2 == 0:
            break
        x1, y1, _ = p["a"]
        x2, y2, _ = p["b"]
        lo_x, hi_x = min(x1, x2) - 1.5, max(x1, x2) + 1.5
        lo_y, hi_y = min(y1, y2) - 1.5, max(y1, y2) + 1.5

        def crosses(it):
            try:
                if is_via(it):
                    q = it.GetPosition()
                    return lo_x <= q.x / mm <= hi_x and lo_y <= q.y / mm <= hi_y
                s, e = it.GetStart(), it.GetEnd()
                sx, sy, ex, ey = s.x / mm, s.y / mm, e.x / mm, e.y / mm
                return not (max(sx, ex) < lo_x or min(sx, ex) > hi_x or
                            max(sy, ey) < lo_y or min(sy, ey) > hi_y)
            except AttributeError:
                return False

        # any copper is evictable. Endpoint-focused: a walled-in BGA ball
        # needs the vias AROUND ITS ANCHOR gone, not some shortest stub that
        # happens to cross the corridor. Evict up to 3 items near an anchor.
        def near_anchor(it):
            try:
                if is_via(it):
                    q = it.GetPosition()
                    pts = [(q.x / mm, q.y / mm)]
                else:
                    s, e = it.GetStart(), it.GetEnd()
                    pts = [(s.x / mm, s.y / mm), (e.x / mm, e.y / mm)]
                for px, py in pts:
                    for ax, ay in ((x1, y1), (x2, y2)):
                        if abs(px - ax) <= 1.6 and abs(py - ay) <= 1.6:
                            return True
                return False
            except AttributeError:
                return False

        # vias report GetLength()==0 — the old >0 filter made every dog-bone
        # untouchable while sealed balls needed exactly those evicted; and
        # sorting by length picked ARBITRARY vias (all tie at 0), not the
        # ones sealing this anchor — sort by distance to the nearest anchor
        def dist_to_anchor(it):
            try:
                if is_via(it):
                    q = it.GetPosition()
                    pts = [(q.x / mm, q.y / mm)]
                else:
                    s, e = it.GetStart(), it.GetEnd()
                    pts = [((s.x + e.x) / 2 / mm, (s.y + e.y) / 2 / mm)]
                return min((px - ax) ** 2 + (py - ay) ** 2
                           for px, py in pts
                           for ax, ay in ((x1, y1), (x2, y2))) ** 0.5
            except AttributeError:
                return 99.0

        victims = sorted((it for it in board.GetTracks()
                          if (crosses(it) or near_anchor(it))
                          and (is_via(it) or it.GetLength() > 0)),
                         key=dist_to_anchor)[:10]
        if not victims:
            continue
        for v in victims:
            board.Remove(v)
        ob2 = Obstacles()          # fresh model after the eviction
        mark_own_copper(ob2)
        # accept when the PAIR closed (try_route_pair verified the drop
        # internally); evicted victims may re-open their own pairs, which
        # the re-sweep below re-routes — requiring an aggregate improvement
        # here rejected every successful rip-up when a victim reopened
        if try_route_pair(ob2, p):
            rip += 1
            count2 = ratsnest_count()
            # the victim's net may now be open — re-sweep everything left
            pairs2, cnt, _ = audit_pairs()
            sweep(ob2, pairs2)
            count2 = ratsnest_count()
        else:
            for v in victims:
                board.Add(v)       # put them back

    # FINAL MILE: the last few pairs get a nuclear clearing — evict every
    # evictable copper within 0.7mm of the straight corridor OR within 2.2mm
    # of either anchor (segment distance: midpoints missed long tracks
    # hugging past an anchor), route the pair on the emptied field, then a
    # full re-sweep re-closes the evicted nets. Pads/zones are sacred.
    mile = 0
    import os as _os
    _MILE = _os.environ.get("FLUXNT_MILE", "0") == "1"
    for p in ([q for q in STATS["failed"]] if _MILE else []):
        if ratsnest_count() == 0:
            break
        x1, y1, _ = p["a"]
        x2, y2, _ = p["b"]

        def victim_dist(it):
            if is_via(it):
                q = it.GetPosition()
                vx, vy = q.x / mm, q.y / mm
                d_ch = pt_seg_dist(vx, vy, x1, y1, x2, y2)
                d_an = min(((vx - x1) ** 2 + (vy - y1) ** 2) ** 0.5,
                           ((vx - x2) ** 2 + (vy - y2) ** 2) ** 0.5)
                return d_ch, d_an
            s, e = it.GetStart(), it.GetEnd()
            a2 = (s.x / mm, s.y / mm)
            b2 = (e.x / mm, e.y / mm)
            d_ch = seg_seg_dist(a2, b2, (x1, y1), (x2, y2))
            d_an = min(pt_seg_dist(x1, y1, a2[0], a2[1], b2[0], b2[1]),
                       pt_seg_dist(x2, y2, a2[0], a2[1], b2[0], b2[1]))
            return d_ch, d_an

        victims = []
        for it in list(board.GetTracks()):
            try:
                d_ch, d_an = victim_dist(it)
            except AttributeError:
                continue
            if d_ch <= 0.7 or d_an <= 2.2:
                victims.append(it)
        if not victims:
            continue
        for v in victims:
            board.Remove(v)
        ob3 = Obstacles()
        mark_own_copper(ob3)
        # orthogonal-only through the cleared channel: no diagonal corners
        # means nothing for the exact commit checks to reject
        if try_route_pair(ob3, p, no_diag=True):
            mile += 1
        else:
            for v in victims:
                board.Add(v)

    # re-sweep everything that the final mile (and rip-ups) re-opened
    pairs3, _, _ = audit_pairs()
    ob4 = Obstacles()
    mark_own_copper(ob4)
    sweep(ob4, pairs3)

    fill_zones()
    final = ratsnest_count()
    board.Save(PCB)
    print(json.dumps({
        "start": count0, "modfan": modfan, "dogbone": dogboned, "stitched": stitched,
        "closed": closed,
        "after_sweep": count2, "ripups": rip, "final_mile": mile,
        "final": final,
        "failed_nets": sorted({p["net"] for p in STATS["failed"]})[:12],
        "why": STATS["why"],
        "zones_filled": filled, "time_s": round(time.time() - T0, 1),
    }))


main()
