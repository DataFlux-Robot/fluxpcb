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
def add_track(sx, sy, ex, ey, layer_id, net):
    if abs(ex - sx) < 0.01 and abs(ey - sy) < 0.01:
        return None
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(int(sx * mm), int(sy * mm)))
    t.SetEnd(pcbnew.VECTOR2I(int(ex * mm), int(ey * mm)))
    t.SetWidth(int(track_w(net.GetNetname() or "") * mm))
    t.SetLayer(layer_id)
    t.SetNet(net)
    board.Add(t)
    return t


def add_via(x, y, net):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(int(x * mm), int(y * mm)))
    v.SetWidth(int(0.5 * mm))
    v.SetDrill(int(0.25 * mm))
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

    def via_exact_ok(self, x, y, nc):
        for li in (0, 1):
            for net, a, b, extra in self.segs[li]:
                if net == nc:
                    continue
                if seg_seg_dist((x, y), (x, y), a, b) < VIA_ANNULUS + 0.15 + extra:
                    return False
            for net, rect in self.rects[li]:
                if net == nc:
                    continue
                if pt_rect_dist(x, y, rect) < VIA_ANNULUS + 0.25:
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


def astar(ob, nc, a, b, w_self):
    """Grid A* a->b with via moves. Own-net copper near b are goals.
    Returns list of committed board items, or None."""
    x1, y1, l1 = a
    x2, y2, l2 = b
    # own-net copper near BOTH anchors is passable + goal-worthy
    start = to_cell(ob, x1, y1, l1, nc)
    if start is None:
        return None
    tgx, tgy = x2 / GRID, y2 / GRID
    # goals = the TARGET PAD's own copper only (1.2mm). A 3mm radius also
    # caught SOURCE-island copper snaking near the target — the A* looped
    # back home, fired the goal test and committed a no-closure path.
    rad = int(1.2 / GRID)
    goals = set()
    for li in (0, 1):
        for gx in range(max(1, int(tgx) - rad), min(ob.nx - 1, int(tgx) + rad)):
            for gy in range(max(1, int(tgy) - rad), min(ob.ny - 1, int(tgy) + rad)):
                if ob.cell_is_own(li, gx, gy, nc):
                    goals.add((li, gx, gy))
    if not goals:
        # target pad has no marked own copper (unrouted SMD on B?) — fall
        # back to the pad cell itself; the end stub will land on it
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
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
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
                hx = abs(gx2 - int(tgx)) + abs(gy2 - int(tgy))
                # weighted A* (W=2): far fewer expansions for slightly
                # suboptimal paths — the plain form burned the whole node
                # budget on goals the flood fill proved reachable
                heapq.heappush(heap, (cur + step + 2.0 * hx * GRID, count, nk))
        nk = (1 - lay, gx, gy)
        if (ob.free(1 - lay, gx, gy, nc) and ob.via_cell_ok(1 - lay, gx, gy)
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
    pending = []
    k = 0
    while k < len(pts) - 1:
        li, px, py = pts[k]
        li2, qx, qy = pts[k + 1]
        if li2 != li:
            # layer switch NEEDS the via — a silently skipped via commits a
            # disconnected two-layer path (389 verify-fails before this fix)
            if not ob.via_exact_ok(px, py, nc):
                for it in items:
                    board.Remove(it)
                return None
            items.append(add_via(px, py, net))
            t1 = add_track(px, py, qx, qy, lid(li), net)
            if t1:
                items.append(t1)
            k += 1
            continue
        j = k + 1
        while j < len(pts) - 1:
            l3, ax, ay = pts[j]
            l4, bx, by = pts[j + 1]
            if l3 != li or l4 != li:
                break
            if abs((bx - px) * (qy - py) - (by - py) * (qx - px)) > 1e-6:
                break
            j += 1
        _, ex, ey = pts[j]
        pending.append((li, px, py, ex, ey))
        k = j
    for li, px, py, ex, ey in pending:
        # only diagonal segments can clip corners (half-rule); axis-aligned
        # segments are grid-guaranteed — the exact scan of every segment
        # made a session take 2.6 HOURS
        if abs(ex - px) < 0.01 or abs(ey - py) < 0.01:
            continue
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


def try_route_pair(ob, pair):
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
        if verify_count() < before:
            ob.add_placed(items)
            STATS["session_tracks"].extend(items)
            return True
        for it in items:
            board.Remove(it)
    # A* fallback
    path = astar(ob, nc, pair["a"], pair["b"], w)
    if path:
        if verify_count() < before:
            ob.add_placed(path)
            STATS["session_tracks"].extend(path)
            return True
        for it in path:
            board.Remove(it)
        _why("verify-fail")
        return False
    return False


# ================================================================ stitching
def power_stitch(ob, pairs):
    """Vias tying fragmented power pads to their inner planes. Two-layer
    exact-checked; committed only when the ratsnest drops."""
    done = 0
    for p in pairs:
        if NET_CLASS.get(p["net"]) != "power":
            continue
        net = board.FindNet(p["net"])
        nc = p["nc"]
        for x, y, lay in (p["a"], p["b"]):
            before = verify_count()
            placed = False
            for dx, dy in ((0, 0), (0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6)):
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


def main():
    filled = fill_zones()
    pairs, count0, frag = audit_pairs()
    ob = Obstacles()
    mark_own_copper(ob)
    stitched = power_stitch(ob, pairs)
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

        victims = sorted((it for it in board.GetTracks()
                          if (crosses(it) or near_anchor(it))
                          and it.GetLength() > 0),
                         key=lambda it: it.GetLength())[:3]
        if not victims:
            continue
        for v in victims:
            board.Remove(v)
        ob2 = Obstacles()          # fresh model after the eviction
        mark_own_copper(ob2)
        before = ratsnest_count()
        if try_route_pair(ob2, p) and ratsnest_count() <= before:
            rip += 1
            count2 = ratsnest_count()
            # the victim's net may now be open — re-sweep everything left
            pairs2, cnt, _ = audit_pairs()
            sweep(ob2, pairs2)
            count2 = ratsnest_count()
        else:
            for v in victims:
                board.Add(v)       # put them back

    fill_zones()
    final = ratsnest_count()
    board.Save(PCB)
    print(json.dumps({
        "start": count0, "stitched": stitched, "closed": closed,
        "after_sweep": count2, "ripups": rip, "final": final,
        "failed_nets": sorted({p["net"] for p in STATS["failed"]})[:12],
        "why": STATS["why"],
        "zones_filled": filled, "time_s": round(time.time() - T0, 1),
    }))


main()
