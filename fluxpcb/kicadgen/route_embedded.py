# Collision-aware router — runs INSIDE KiCad's Python (invoked by fluxpcb.routing).
# For each unconnected pair: try L-route variants, verify real geometric
# clearance against all other-net copper (segments + pads + vias), fall back
# to opposite layer, else leave the pair unrouted (hand-fixable, ignored by
# calibrated severities).
# Pair order = critical -> normal -> power (routing_classes knowhow); per-net
# track width from the classes file (power 0.4mm, critical IPC width).
import json
import sys
import pcbnew

PCB = sys.argv[1]
PAIRS = sys.argv[2]
CLS = sys.argv[3] if len(sys.argv) > 3 else ""

board = pcbnew.LoadBoard(PCB)
mm = 1000000  # KiCad nm units

CLEAR_MM = 0.25          # center-line clearance to other-net copper
_PTH = getattr(pcbnew, "PAD_ATTRIB_PTH",
             getattr(pcbnew, "PAD_ATTRIB_THROUGH_HOLE", 3))  # K10 rename
TRACK_W = 0.2

_NET_CLASS = {}
try:
    with open(CLS) as _f:
        _NET_CLASS = json.load(_f).get("classes", {})
except (OSError, ValueError):
    pass
_WIDTHS = {"critical": 0.15, "normal": 0.2, "power": 0.4}


def track_w(net_name):
    return _WIDTHS.get(_NET_CLASS.get(net_name, "normal"), TRACK_W)


def find_net(name):
    return board.FindNet(name)


# ---------------------------------------------------------------- geometry (mm)
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
    if abs(d) < 1e-12:  # parallel
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


# ---------------------------------------------------------------- obstacles
def is_via(item):
    # KiCad 10 python: no IsVia(); Type() enum is the reliable check
    return item.Type() == pcbnew.PCB_VIA_T


def pad_rect(pad):
    """(cx, cy, hw, hh, ang) — center, half-extents, orientation in rad."""
    p = pad.GetPosition()
    try:
        sz = pad.GetSize()
        w, h = sz.x / mm, sz.y / mm
    except AttributeError:
        bb = pad.GetBoundingBox()
        w, h = bb.GetWidth() / mm, bb.GetHeight() / mm
    ang = pad.GetOrientation()
    try:
        ang = ang.AsRadians()          # KiCad 10: EDA_ANGLE
    except AttributeError:
        ang = float(ang or 0.0)
    return (p.x / mm, p.y / mm, w / 2.0, h / 2.0, ang)


def pt_rect_dist(px, py, rect):
    cx, cy, hw, hh, ang = rect
    if ang:
        ca, sa = __import__("math").cos(ang), __import__("math").sin(ang)
        dx, dy = px - cx, py - cy
        rx = dx * ca + dy * sa
        ry = -dx * sa + dy * ca
    else:
        rx, ry = px - cx, py - cy
    ex = max(abs(rx) - hw, 0.0)
    ey = max(abs(ry) - hh, 0.0)
    return (ex * ex + ey * ey) ** 0.5


def seg_hits_rect(x1, y1, x2, y2, rect, clear_mm):
    # bounding-box prefilter
    cx, cy, hw, hh, _ = rect
    m = hw + hh + clear_mm + 0.5
    if min(x1, x2) > cx + m or max(x1, x2) < cx - m:
        return False
    if min(y1, y2) > cy + m or max(y1, y2) < cy - m:
        return False
    n = max(2, int((((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5) / 0.05))
    for i in range(n + 1):
        t = i / n
        if pt_rect_dist(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, rect) < clear_mm:
            return True
    return False


def collect_obstacles(layer_id):
    """Other-net copper per layer: segments (net, a, b, extra_clear) and pads."""
    segs, rects = [], []
    for item in board.GetTracks():
        try:
            net = item.GetNetCode()
            if is_via(item):
                p = item.GetPosition()
                c = (p.x / mm, p.y / mm)
                segs.append((net, c, c, 0.3))   # via radius on top of CLEAR_MM
            else:
                if item.GetLayer() != layer_id:
                    continue
                s, e = item.GetStart(), item.GetEnd()
                # half-width beyond the 0.1mm baseline widens keepout
                try:
                    w_extra = max(0.0, item.GetWidth(pcbnew.F_Cu) / mm / 2 - 0.1)
                except TypeError:
                    w_extra = max(0.0, item.GetWidth() / mm / 2 - 0.1)
                segs.append((net, (s.x / mm, s.y / mm), (e.x / mm, e.y / mm),
                             w_extra))
        except AttributeError:
            continue
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                if not pad.IsOnLayer(layer_id):
                    continue    # F.Cu SMD pads must not block B.Cu routing
                rects.append((pad.GetNetCode(), pad_rect(pad)))
            except AttributeError:
                continue
    return segs, rects


def clear(sx, sy, ex, ey, layer_id, net_code, segs, rects, self_w=None):
    """True if the segment clears every other-net obstacle on this layer.
    self_w: the width of the track being placed (power nets are wider and
    need proportionally more center-line distance)."""
    own = max(0.0, (self_w or TRACK_W) / 2 - 0.1)
    for net, a, b, extra in segs:
        if net == net_code:
            continue
        if seg_seg_dist((sx, sy), (ex, ey), a, b) < CLEAR_MM + extra + own:
            return False
    for net, rect in rects:
        if net == net_code:
            continue
        if seg_hits_rect(sx, sy, ex, ey, rect, CLEAR_MM):
            return False
    return True


def add_track(sx, sy, ex, ey, layer_id, net):
    if abs(ex - sx) < 0.01 and abs(ey - sy) < 0.01:
        return None                      # degenerate zero-length segment
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(int(sx * mm), int(sy * mm)))
    t.SetEnd(pcbnew.VECTOR2I(int(ex * mm), int(ey * mm)))
    t.SetWidth(int(track_w(net.GetNetname() or "") * mm))
    t.SetLayer(layer_id)
    t.SetNet(net)
    board.Add(t)
    return t


def _on_tht_pad(x, y, net_code):
    """True when (x,y) sits on a same-net through-hole pad — the pad itself
    spans both layers, no via needed (avoids co-located drill holes)."""
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                if pad.GetNetCode() != net_code:
                    continue
                if pad.GetAttribute() != _PTH:
                    continue
                p = pad.GetPosition()
                if abs(p.x / mm - x) < 0.4 and abs(p.y / mm - y) < 0.4:
                    return True
            except AttributeError:
                continue
    return False


def add_via(x, y, net, nc):
    if _on_tht_pad(x, y, nc):
        return None
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(int(x * mm), int(y * mm)))
    v.SetWidth(int(0.6 * mm))
    v.SetDrill(int(0.3 * mm))
    v.SetNet(net)
    board.Add(v)
    return v


def normalize_vias():
    """Enforce 0.6/0.3 on every via (FR injection and history can leave
    zero-annular 0.3/0.3 drills that fail via_diameter/annular_width)."""
    fixed = 0
    for item in board.GetTracks():
        if item.Type() != pcbnew.PCB_VIA_T:
            continue
        try:
            # KiCad 10: via GetWidth() REQUIRES a layer argument (asserts
            # headless otherwise); vias span F..B so F_Cu represents them
            if item.GetWidth(pcbnew.F_Cu) < int(0.6 * mm):
                item.SetWidth(int(0.6 * mm))
                fixed += 1
            if item.GetDrillValue() < int(0.3 * mm):
                item.SetDrill(int(0.3 * mm))
                fixed += 1
        except (AttributeError, TypeError):
            continue
    return fixed


def incremental(seg, net_code, layer_id):
    """Register a freshly added segment as an obstacle for later checks."""
    s, e = seg.GetStart(), seg.GetEnd()
    return (net_code, (s.x / mm, s.y / mm), (e.x / mm, e.y / mm))


# ---------------------------------------------------------------- power vias
def _hole_map():
    """All drilled holes (vias + THT pads) as (x, y, radius) — a new via
    must keep >= 0.25mm edge spacing (KiCad hole-to-hole rule)."""
    holes = []
    for item in board.GetTracks():
        if is_via(item):
            try:
                p = item.GetPosition()
                holes.append((p.x / mm, p.y / mm, item.GetDrillValue() / mm / 2))
            except AttributeError:
                pass
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                if pad.GetAttribute() != _PTH:
                    continue
                p = pad.GetPosition()
                holes.append((p.x / mm, p.y / mm,
                              (pad.GetDrillValue() or 0.8) / mm / 2))
            except AttributeError:
                continue
    return holes


def _hole_ok(x, y):
    r1 = 0.15                      # own drill radius (0.3mm via)
    for hx, hy, hr in HOLES:
        if ((hx - x) ** 2 + (hy - y) ** 2) ** 0.5 < hr + r1 + 0.28:
            return False
    return True


def power_via_pass(fragmented):
    """Via for every SMD pad on a power-class net — ties F.Cu pads to their
    inner planes (GND->In1/In4, 3V3->In2, 5V->In3), dissolving the island
    fragmentation the ratsnest reports for power nets (a 3V3 LGA pad above
    the In2 pour is one via away from every other 3V3 pad). THT power pins
    already span all layers; zones anti-pad foreign vias on refill.
    RESTRICTED to nets that are fragmented right now — blanket vias on every
    power pad punch anti-pads into the planes on refill and can re-isolate
    previously-connected pads (observed: GND 0 islands -> 2 after a blanket
    pass). Via at pad center when it clears other-net copper (0.8mm-pitch
    BGA balls), else at a 0.6mm offset (0.5mm-pitch LGA pads), else skipped."""
    added = 0
    segs_f, rects_f = collect_obstacles(pcbnew.F_Cu)
    segs_b, rects_b = collect_obstacles(pcbnew.B_Cu)

    def _via2_ok(x, y, nc):
        # a power via lands on BOTH layers: full two-layer clearance (a GND
        # via-in-pad punching through a B.Cu signal track was the top short
        # source on pass 2+ of the h743-fc board)
        for segs, rects in ((segs_f, rects_f), (segs_b, rects_b)):
            for net2, a, b, extra in segs:
                if net2 == nc:
                    continue
                if seg_seg_dist((x, y), (x, y), a, b) < 0.45 + extra:
                    return False
            for net2, rect in rects:
                if net2 == nc:
                    continue
                if pt_rect_dist(x, y, rect) < 0.55:
                    return False
        return True
    have = set()
    for item in board.GetTracks():
        if is_via(item):
            p = item.GetPosition()
            have.add((round(p.x / mm, 2), round(p.y / mm, 2)))
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                name = pad.GetNetname() or ""
                if _NET_CLASS.get(name) != "power" or name not in fragmented:
                    continue
                if pad.GetAttribute() == _PTH:
                    continue            # spans all layers already
                p = pad.GetPosition()
                x, y = p.x / mm, p.y / mm
                net = board.FindNet(name)
                nc = net.GetNetCode() if net else -1
                w_self = track_w(name)
                for dx, dy in ((0, 0), (0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6)):
                    key = (round(x + dx, 2), round(y + dy, 2))
                    if key in have:
                        continue
                    if not via_ok(x + dx, y + dy, nc, rects_f):
                        continue
                    if not _hole_ok(x + dx, y + dy):
                        continue
                    if not _via2_ok(x + dx, y + dy, nc):
                        continue
                    # offset vias need a stub track to touch the pad
                    if dx or dy:
                        if not clear(x, y, x + dx, y + dy, pcbnew.F_Cu,
                                     nc, segs_f, rects_f, w_self):
                            continue
                    if add_via(x + dx, y + dy, net, nc):
                        if dx or dy:
                            add_track(x, y, x + dx, y + dy, pcbnew.F_Cu, net)
                        added += 1
                        have.add(key)
                        # register the new hole so later candidates in this
                        # same run keep hole-to-hole spacing (0.3mm-apart
                        # same-net vias were observed without this)
                        HOLES.append((x + dx, y + dy, 0.15))
                        break
            except AttributeError:
                continue
    return added


# board edge keepout: tracks/vias must stay >= 0.55mm inside the outline
_bb = board.GetBoardEdgesBoundingBox()
EDGE = (_bb.GetX() / mm, _bb.GetY() / mm,
        (_bb.GetX() + _bb.GetWidth()) / mm, (_bb.GetY() + _bb.GetHeight()) / mm)
EDGE_KEEP = 0.55


def edge_ok(x1, y1, x2, y2):
    for x, y in ((x1, y1), (x2, y2)):
        if not (EDGE[0] + EDGE_KEEP <= x <= EDGE[2] - EDGE_KEEP and
                EDGE[1] + EDGE_KEEP <= y <= EDGE[3] - EDGE_KEEP):
            return False
    return True


def via_ok(x, y, net_code, rects):
    """A via needs pad-edge clearance 0.3(own radius)+0.25 = 0.55mm, plus
    0.25mm edge spacing to every drilled hole (hole-to-hole DRC rule)."""
    if not (EDGE[0] + EDGE_KEEP <= x <= EDGE[2] - EDGE_KEEP and
            EDGE[1] + EDGE_KEEP <= y <= EDGE[3] - EDGE_KEEP):
        return False
    if HOLES and not _hole_ok(x, y):
        return False
    for net, rect in rects:
        if net == net_code:
            continue
        if pt_rect_dist(x, y, rect) < 0.55:
            return False
    return True


def _via_candidates(x, y, lay, other, nc, rects_o, rects_s):
    """Via anchor options for one endpoint. BGA balls admit a CENTER via
    (neighbor 0.8mm away: 0.8-0.2 = 0.6 >= 0.55) but no offset via (lands on
    the neighbor); fine-pitch LGA pads are the reverse. THT pads and
    already-opposite-layer endpoints need no via at all."""
    if lay == other or _on_tht_pad(x, y, nc):
        return [(x, y)]
    cands = []
    if via_ok(x, y, nc, rects_o) and via_ok(x, y, nc, rects_s):
        cands.append((x, y))
    offs = ((0.7, 0), (-0.7, 0), (0, 0.7), (0, -0.7),
            (1.1, 0), (-1.1, 0), (0, 1.1), (0, -1.1))
    for dx, dy in offs:
        vx, vy = x + dx, y + dy
        if via_ok(vx, vy, nc, rects_o) and via_ok(vx, vy, nc, rects_s):
            cands.append((vx, vy))
    return cands


def route_with_offset_vias(x1, y1, l1, x2, y2, l2, net, nc, net_name):
    """Buried-escape fallback: mixed pairs (BGA ball + fine-pitch LGA pad)
    can never satisfy the single-via-style opposite-layer strategy. Pick a
    per-endpoint via anchor (center or offset), escape-stub on the pad
    layer, then L-route between vias on the opposite layer."""
    other = 1 - l1
    lid_own = pcbnew.F_Cu if l1 == 0 else pcbnew.B_Cu
    lid_oth = pcbnew.B_Cu if other == 1 else pcbnew.F_Cu
    w_self = track_w(net_name)
    segs_o, rects_o = collect_obstacles(lid_oth)
    segs_s, rects_s = collect_obstacles(lid_own)
    cand1 = _via_candidates(x1, y1, l1, other, nc, rects_o, rects_s)
    cand2 = _via_candidates(x2, y2, l2, other, nc, rects_o, rects_s)
    for vx1, vy1 in cand1:
        need_via1 = l1 != other and not _on_tht_pad(x1, y1, nc)
        # escape stubs on the endpoint's own layer
        if need_via1 and (vx1, vy1) != (x1, y1) and not clear(
                x1, y1, vx1, vy1, lid_own, nc, segs_s, rects_s, w_self):
            continue
        for vx2, vy2 in cand2:
            need_via2 = l2 != other and not _on_tht_pad(x2, y2, nc)
            if need_via2 and (vx2, vy2) != (x2, y2) and not clear(
                    x2, y2, vx2, vy2, lid_own, nc, segs_s, rects_s, w_self):
                continue
            # L-route between the vias on the other layer
            for mx, my in ((vx2, vy1), (vx1, vy2)):
                if not edge_ok(vx1, vy1, vx2, vy2) or not edge_ok(mx, my, mx, my):
                    continue
                if not clear(vx1, vy1, mx, my, lid_oth, nc,
                             segs_o, rects_o, w_self):
                    continue
                if not clear(mx, my, vx2, vy2, lid_oth, nc,
                             segs_o, rects_o, w_self):
                    continue
                # commit
                if need_via1:
                    if (vx1, vy1) != (x1, y1):
                        add_track(x1, y1, vx1, vy1, lid_own, net)
                    add_via(vx1, vy1, net, nc)
                if need_via2:
                    if (vx2, vy2) != (x2, y2):
                        add_track(x2, y2, vx2, vy2, lid_own, net)
                    add_via(vx2, vy2, net, nc)
                add_track(vx1, vy1, mx, my, lid_oth, net)
                add_track(mx, my, vx2, vy2, lid_oth, net)
                return True
    return False


# ---------------------------------------------------------------- A* maze
# Final fallback per pair: grid A* over both signal layers. The L/Z/offset
# strategies are shape-guesses; a 240-ball BGA needs actual maze routing
# (Freerouting times out headless on these boards).
_ASTAR_GRID = 0.2          # mm per cell (BGA pitch 0.8 = 4 cells)
_ASTAR_CLEAR = 0.16        # occupancy margin beyond copper edge


def _via_full_ok(x, y, nc, segs_f, segs_b, rects_f, rects_b):
    """Via candidate with the FULL clearance story: the 0.6mm annulus must
    clear other-net copper on BOTH layers (a via lands on both). The grid
    margins are tuned for tracks; a via needs 0.3(half)+0.15+half(other)."""
    if not _hole_ok(x, y):
        return False
    for segs in (segs_f, segs_b):
        for net, a, b, extra in segs:
            if net == nc:
                continue
            if seg_seg_dist((x, y), (x, y), a, b) < 0.45 + extra:
                return False
    for rects in (rects_f, rects_b):
        for net, rect in rects:
            if net == nc:
                continue
            if pt_rect_dist(x, y, rect) < 0.55:
                return False
    return True


def _astar_grids():
    """Occupancy + via-block grids for F.Cu/B.Cu.
    occ: 0 free, 255 same-net-ok, 1 blocked (track margins)
    via_block: True where a 0.6mm-annulus via would violate other-net copper
    (0.55mm to pad edges, 0.45mm+half to track centers) or another hole."""
    nx = int((EDGE[2] - EDGE[0]) / _ASTAR_GRID) + 1
    ny = int((EDGE[3] - EDGE[1]) / _ASTAR_GRID) + 1
    occ = [bytearray(nx * ny) for _ in range(2)]
    via_block = [bytearray(nx * ny) for _ in range(2)]
    hole_block = bytearray(nx * ny)
    return occ, via_block, hole_block, nx, ny


def _cell_idx(gx, gy, nx, ny):
    if gx < 1 or gy < 1 or gx >= nx - 1 or gy >= ny - 1:
        return -1
    return gy * nx + gx


def _mark(grids, nx, ny, layer_id, x0, y0, x1, y1, margin, tag,
          via_r=None):
    """Rasterize a copper shape (segment) into occ (+ via_block at via_r)."""
    if layer_id == pcbnew.F_Cu:
        lay = 0
    elif layer_id == pcbnew.B_Cu:
        lay = 1
    else:
        lay = -1               # via: both layers
    gx0 = int((min(x0, x1) - margin) / _ASTAR_GRID)
    gx1 = int((max(x0, x1) + margin) / _ASTAR_GRID) + 1
    gy0 = int((min(y0, y1) - margin) / _ASTAR_GRID)
    gy1 = int((max(y0, y1) + margin) / _ASTAR_GRID) + 1
    m2 = max(margin, via_r or 0)
    gx0b = int((min(x0, x1) - m2) / _ASTAR_GRID)
    gx1b = int((max(x0, x1) + m2) / _ASTAR_GRID) + 1
    gy0b = int((min(y0, y1) - m2) / _ASTAR_GRID)
    gy1b = int((max(y0, y1) + m2) / _ASTAR_GRID) + 1
    occ, via_block = grids
    for lay_i in ((0, 1) if lay < 0 else (lay,)):
        g, vb = occ[lay_i], via_block[lay_i]
        for gx in range(max(0, gx0b), min(nx, gx1b)):
            cx = (gx + 0.5) * _ASTAR_GRID
            for gy in range(max(0, gy0b), min(ny, gy1b)):
                cy = (gy + 0.5) * _ASTAR_GRID
                d = pt_seg_dist(cx, cy, x0, y0, x1, y1)
                i = gy * nx + gx
                if d <= margin and g[i] != 255:
                    g[i] = tag
                if via_r and d <= via_r:
                    vb[i] = 1
    return None


def _mark_own(grids, nx, ny, layer_id, x0, y0, x1, y1, half_w):
    """Same-net copper: passable AND a potential goal."""
    _mark(grids, nx, ny, layer_id, x0, y0, x1, y1, half_w, 255)


def _mark_holes(hole_block, nx, ny, x, y, r=0.6):
    """Hole keepout for via placement: own drill 0.3 + clearance 0.25 +
    other hole radius (~0.5 THT) → 0.6mm default radius from hole center."""
    gx0, gx1 = int((x - r) / _ASTAR_GRID), int((x + r) / _ASTAR_GRID) + 1
    gy0, gy1 = int((y - r) / _ASTAR_GRID), int((y + r) / _ASTAR_GRID) + 1
    for gx in range(max(0, gx0), min(nx, gx1)):
        for gy in range(max(0, gy0), min(ny, gy1)):
            cx, cy = (gx + 0.5) * _ASTAR_GRID, (gy + 0.5) * _ASTAR_GRID
            if (cx - x) ** 2 + (cy - y) ** 2 <= r * r:
                hole_block[gy * nx + gx] = 1


def hw_of(item):
    try:
        return item.GetWidth(pcbnew.F_Cu) / mm / 2
    except TypeError:
        try:
            return item.GetWidth() / mm / 2
        except TypeError:
            return 0.1


def route_astar(x1, y1, l1, x2, y2, l2, net, nc, net_name):
    """Grid A* with via moves. Returns True when the pair got connected.
    Via-validity comes from precomputed via_block grids — a per-node scan
    of every obstacle timed out (400k nodes x 1k obstacles)."""
    import heapq
    w_self = track_w(net_name)
    own = w_self / 2 + _ASTAR_CLEAR
    segs_f, rects_f = collect_obstacles(pcbnew.F_Cu)
    VIA_R = 0.55                 # via center to other-net copper edge
    occ, via_block, hole_block, nx, ny = _astar_grids()
    grids = (occ, via_block)     # _mark() unpacks this pair

    # obstacles: other-net copper; own-net copper marked passable (255)
    for item in board.GetTracks():
        try:
            inc = item.GetNetCode()
            if is_via(item):
                p = item.GetPosition()
                if inc == nc:
                    _mark_own(grids, nx, ny, None, p.x / mm, p.y / mm,
                              p.x / mm, p.y / mm, 0.3)
                else:
                    _mark(grids, nx, ny, None, p.x / mm, p.y / mm,
                          p.x / mm, p.y / mm, 0.3 + own, 1, via_r=0.85)
                    _mark_holes(hole_block, nx, ny, p.x / mm, p.y / mm, r=0.9)
                continue
            lid = item.GetLayer()
            s, e = item.GetStart(), item.GetEnd()
            if inc == nc:
                _mark_own(grids, nx, ny, lid, s.x / mm, s.y / mm,
                          e.x / mm, e.y / mm, 0.1)
            else:
                _mark(grids, nx, ny, lid, s.x / mm, s.y / mm,
                      e.x / mm, e.y / mm, 0.1 + own, 1, via_r=0.55 + hw_of(item))
        except AttributeError:
            continue
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                inc = pad.GetNetCode()
                cx, cy, hw, hh, ang = pad_rect(pad)
                for lid in (pcbnew.F_Cu, pcbnew.B_Cu):
                    if not pad.IsOnLayer(lid):
                        continue
                    if inc == nc:
                        _mark_own(grids, nx, ny, lid, cx - hw, cy, cx + hw, cy,
                                  max(hw, hh) + 0.3)
                    else:
                        _mark(grids, nx, ny, lid, cx - hw, cy, cx + hw, cy,
                              max(hw, hh) + own, 1,
                              via_r=VIA_R + max(hw, hh))
                if inc != nc and pad.GetAttribute() == _PTH:
                    try:
                        dr = (pad.GetDrillValue() or 1000000) / mm / 2
                    except AttributeError:
                        dr = 0.5
                    _mark_holes(hole_block, nx, ny, cx, cy, r=dr + 0.4)
            except AttributeError:
                continue

    def free(lay, gx, gy):
        i = _cell_idx(gx, gy, nx, ny)
        return i >= 0 and (occ[lay][i] in (0, 255))

    def cell_via_ok(lay, gx, gy):
        i = _cell_idx(gx, gy, nx, ny)
        return (i >= 0 and not via_block[lay][i] and not hole_block[i]
                and not via_block[1 - lay][i])

    def to_cell(x, y, lay):
        gx, gy = int(x / _ASTAR_GRID), int(y / _ASTAR_GRID)
        # nearest free cell within a small window (pad anchors are often
        # inside their own copper, which is marked 255=passable anyway)
        for r in range(0, 6):
            for dx in range(-r, r + 1):
                for dy in (range(-r, r + 1) if abs(dx) == r else (0,)):
                    gx2, gy2 = gx + dx, gy + dy
                    if free(lay, gx2, gy2):
                        return gx2, gy2
        return None

    start = to_cell(x1, y1, 0)          # start layer F.Cu anchor
    if start is None:
        return False
    # goal cells: same-net copper NEAR THE TARGET ANCHOR ONLY — the source
    # island's own copper is also marked 255 and must never count as arrival
    goal_keys = set()
    tgx, tgy = x2 / _ASTAR_GRID, y2 / _ASTAR_GRID
    rad = int(3.0 / _ASTAR_GRID)
    gx0, gx1 = int(tgx) - rad, int(tgx) + rad
    gy0, gy1 = int(tgy) - rad, int(tgy) + rad
    for lay in (0, 1):
        for gx in range(max(1, gx0), min(nx - 1, gx1)):
            for gy in range(max(1, gy0), min(ny - 1, gy1)):
                i = gy * nx + gx
                if occ[lay][i] == 255:
                    goal_keys.add((lay, gx, gy))
    if not goal_keys:
        return False

    sx, sy = start
    open_heap = [(0.0, 0, (0, sx, sy))]
    came, gscore = {}, {(0, sx, sy): 0.0}
    counter = 0
    VIA_COST = 30.0
    found = None
    while open_heap:
        _, _, (lay, gx, gy) = heapq.heappop(open_heap)
        if (lay, gx, gy) in goal_keys:
            found = (lay, gx, gy)
            break
        cur = gscore[(lay, gx, gy)]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            gx2, gy2 = gx + dx, gy + dy
            if not free(lay, gx2, gy2):
                continue
            if dx and dy and not (free(lay, gx + dx, gy)
                                  and free(lay, gx, gy + dy)):
                continue         # diagonal must not clip a blocked corner
            step = _ASTAR_GRID * (1.4142 if dx and dy else 1.0)
            nk = (lay, gx2, gy2)
            if gscore.get(nk, 1e18) > cur + step:
                gscore[nk] = cur + step
                came[nk] = (lay, gx, gy)
                counter += 1
                hx = abs(gx2 - int(tgx)) + abs(gy2 - int(tgy))
                heapq.heappush(open_heap, (cur + step + hx * _ASTAR_GRID,
                                           counter, nk))
        # via move — O(1) grid lookup (annulus validity precomputed)
        nk = (1 - lay, gx, gy)
        if (free(1 - lay, gx, gy) and cell_via_ok(1 - lay, gx, gy)
                and gscore.get(nk, 1e18) > cur + VIA_COST):
            gscore[nk] = cur + VIA_COST
            came[nk] = (lay, gx, gy)
            counter += 1
            heapq.heappush(open_heap, (cur + VIA_COST, counter, nk))
        if counter > 400000:
            return False
    if not found:
        return False

    # reconstruct: (lay, gx, gy) chain -> segments + vias
    chain = []
    node = found
    while node and node != (0, sx, sy):
        chain.append(node)
        node = came.get(node)
    chain.append((0, sx, sy))
    chain.reverse()
    # BOTH stubs (pad anchors -> first/last cell centers) are clearance-
    # checked BEFORE anything is committed — a goal cell can sit up to
    # ~0.3mm from the target copper's edge; without the end stub the path
    # never actually touches it and the ratsnest open stays
    fx, fy = (chain[0][1] + 0.5) * _ASTAR_GRID, (chain[0][2] + 0.5) * _ASTAR_GRID
    lx, ly = (chain[-1][1] + 0.5) * _ASTAR_GRID, (chain[-1][2] + 0.5) * _ASTAR_GRID
    end_lay = chain[-1][0]
    lid = lambda lay: pcbnew.F_Cu if lay == 0 else pcbnew.B_Cu
    segs_e, rects_e = collect_obstacles(lid(end_lay))
    if not clear(x1, y1, fx, fy, pcbnew.F_Cu, nc, segs_f, rects_f, w_self):
        return False           # would short; leave the pair for the next pass
    if not clear(lx, ly, x2, y2, lid(end_lay), nc, segs_e, rects_e, w_self):
        return False
    add_track(x1, y1, fx, fy, pcbnew.F_Cu, net)
    add_track(lx, ly, x2, y2, lid(end_lay), net)
    # merge collinear runs
    segs = []
    for k, (lay, gx, gy) in enumerate(chain):
        px, py = (gx + 0.5) * _ASTAR_GRID, (gy + 0.5) * _ASTAR_GRID
        segs.append((lay, px, py))
    out, via_pts = [], []
    k = 0
    while k < len(segs) - 1:
        lay, px, py = segs[k]
        lay2, qx, qy = segs[k + 1]
        if lay2 != lay:
            via_pts.append((lay, px, py))
            out.append((lay, px, py, qx, qy))       # vertical via cell
            out.append((lay2, px, py, qx, qy))
            k += 1
            continue
        # extend while collinear on the same layer
        j = k + 1
        while j < len(segs) - 1:
            l3, ax, ay = segs[j]
            l4, bx, by = segs[j + 1]
            if l3 != lay or l4 != lay:
                break
            cross = (bx - px) * (qy - py) - (by - py) * (qx - px)
            if abs(cross) > 1e-6:
                break
            j += 1
        layE, ex, ey = segs[j]
        out.append((lay, px, py, ex, ey))
        k = j
    for lay, ax, ay, bx, by in out:
        add_track(ax, ay, bx, by, lid(lay), net)
    for lay, vx, vy in via_pts:
        add_via(vx, vy, net, nc)
    # stub to the target copper: the goal cell sits ON same-net copper, the
    # track already touches it by construction
    return True


added = failed = skipped = 0
# read pairs first: their nets define what is fragmented RIGHT NOW (the
# power-via pass must stay targeted — blanket vias destabilize zone refills)
_pair_lines = [l.strip() for l in open(PAIRS) if l.strip()]
_fragmented = {l.split("|", 1)[0] for l in _pair_lines if "|" in l}
HOLES = _hole_map()
power_vias = power_via_pass(_fragmented)
with open(PAIRS) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        net_name = parts[0]
        x1, y1, l1 = float(parts[1]), float(parts[2]), int(parts[3])
        x2, y2, l2 = float(parts[4]), float(parts[5]), int(parts[6])
        net = find_net(net_name)
        if not net:
            print("WARN: net {} not found".format(net_name))
            continue
        nc = net.GetNetCode()

        strategies = []
        for lay in {l1, l2}:
            strategies.append((lay, x2, y1))    # L: horizontal-first
            strategies.append((lay, x1, y2))    # L: vertical-first
            strategies.append((lay, (x1 + x2) / 2, y1))  # Z-ish
        # lane offsets: parallel same-row pins (USB pairs, bus groups) would
        # otherwise run collinear H-first legs into each other — stagger them
        for off in (1.27, -1.27, 2.54):
            strategies.append((l1, x2, y1 + off))
        # opposite-layer with via at the far end
        other = 1 - l1
        strategies.append((other, x2, y1))
        strategies.append((other, x1, y2))

        routed = False
        for lay, mx, my in strategies:
            lid = pcbnew.F_Cu if lay == 0 else pcbnew.B_Cu
            segs, rects = collect_obstacles(lid)
            if not edge_ok(x1, y1, x2, y2) or not edge_ok(mx, my, mx, my):
                continue
            need_via_start = (lay != l1)
            need_via_end = (lay != l2)
            if need_via_start and not via_ok(x1, y1, nc, rects):
                continue
            if need_via_end and not via_ok(x2, y2, nc, rects):
                continue
            w_self = track_w(net_name)
            own = max(0.0, w_self / 2 - 0.1)
            ok1 = clear(x1, y1, mx, my, lid, nc, segs, rects, w_self)
            # check leg2 with leg1 as a virtual obstacle — add only if both pass
            if ok1 and (abs(mx - x1) >= 0.01 or abs(my - y1) >= 0.01):
                segs.append((nc, (x1, y1), (mx, my), own))
            ok2 = clear(mx, my, x2, y2, lid, nc, segs, rects, w_self)
            if not (ok1 and ok2):
                continue
            add_track(x1, y1, mx, my, lid, net)
            add_track(mx, my, x2, y2, lid, net)
            if lay != l1:  # needed a layer switch: via at start
                add_via(x1, y1, net, nc)
            if lay != l2:
                add_via(x2, y2, net, nc)
            routed = True
            added += 1
            break
        if not routed:
            if route_with_offset_vias(x1, y1, l1, x2, y2, l2, net, nc,
                                      net_name):
                routed = True
                added += 1
        if not routed:
            try:
                if route_astar(x1, y1, l1, x2, y2, l2, net, nc, net_name):
                    routed = True
                    added += 1
            except Exception:
                pass            # A* is best-effort; pair stays skipped
        if not routed:
            skipped += 1

# orphan sweep: KiCad occasionally persists added items with an empty net
# (degenerate geometry edge cases) — anything netless or zero-length goes.
orphans = []
orphan_kinds = {}
for item in board.GetTracks():
    try:
        name = item.GetNetname()
        if not name:
            key = type(item).__name__
            orphan_kinds[key] = orphan_kinds.get(key, 0) + 1
            orphans.append(item)
            continue
        if is_via(item):
            continue
        s, e = item.GetStart(), item.GetEnd()
        if abs(s.x - e.x) < 1000 and abs(s.y - e.y) < 1000:
            orphans.append(item)
    except AttributeError:
        pass
for item in orphans:
    board.Remove(item)

via_fixed = normalize_vias()

board.Save(PCB)
print("ROUTES_ADDED={} SKIPPED={} POWER_VIAS={} ORPHANS_REMOVED={} VIAS_FIXED={} KINDS={}".format(
    added, skipped, power_vias, len(orphans), via_fixed, orphan_kinds))
