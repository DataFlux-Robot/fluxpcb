# Collision-aware router — runs INSIDE KiCad's Python (invoked by fluxpcb.routing).
# For each unconnected pair: try L-route variants, verify real geometric
# clearance against all other-net copper (segments + pads + vias), fall back
# to opposite layer, else leave the pair unrouted (hand-fixable, ignored by
# calibrated severities).
import sys
import pcbnew

PCB = sys.argv[1]
PAIRS = sys.argv[2]

board = pcbnew.LoadBoard(PCB)
mm = 1000000  # KiCad nm units

CLEAR_MM = 0.25          # center-line clearance to other-net copper
TRACK_W = 0.2


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
                segs.append((net, (s.x / mm, s.y / mm), (e.x / mm, e.y / mm), 0.0))
        except AttributeError:
            continue
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                rects.append((pad.GetNetCode(), pad_rect(pad)))
            except AttributeError:
                continue
    return segs, rects


def clear(sx, sy, ex, ey, layer_id, net_code, segs, rects):
    """True if the segment clears every other-net obstacle on this layer."""
    for net, a, b, extra in segs:
        if net == net_code:
            continue
        if seg_seg_dist((sx, sy), (ex, ey), a, b) < CLEAR_MM + extra:
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
    t.SetWidth(int(TRACK_W * mm))
    t.SetLayer(layer_id)
    t.SetNet(net)
    board.Add(t)
    return t


def add_via(x, y, net):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(int(x * mm), int(y * mm)))
    v.SetWidth(int(0.6 * mm), int(0.3 * mm))
    v.SetNet(net)
    board.Add(v)
    return v


def incremental(seg, net_code, layer_id):
    """Register a freshly added segment as an obstacle for later checks."""
    s, e = seg.GetStart(), seg.GetEnd()
    return (net_code, (s.x / mm, s.y / mm), (e.x / mm, e.y / mm))


added = failed = skipped = 0
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
        # opposite-layer with via at the far end
        other = 1 - l1
        strategies.append((other, x2, y1))
        strategies.append((other, x1, y2))

        routed = False
        for lay, mx, my in strategies:
            lid = pcbnew.F_Cu if lay == 0 else pcbnew.B_Cu
            segs, rects = collect_obstacles(lid)
            ok1 = clear(x1, y1, mx, my, lid, nc, segs, rects)
            # check leg2 with leg1 as a virtual obstacle — add only if both pass
            if ok1 and (abs(mx - x1) >= 0.01 or abs(my - y1) >= 0.01):
                segs.append((nc, (x1, y1), (mx, my), 0.0))
            ok2 = clear(mx, my, x2, y2, lid, nc, segs, rects)
            if not (ok1 and ok2):
                continue
            add_track(x1, y1, mx, my, lid, net)
            add_track(mx, my, x2, y2, lid, net)
            if lay != l1:  # needed a layer switch: via at start
                add_via(x1, y1, net)
            if lay != l2:
                add_via(x2, y2, net)
            routed = True
            added += 1
            break
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

board.Save(PCB)
print("ROUTES_ADDED={} SKIPPED={} ORPHANS_REMOVED={} KINDS={}".format(
    added, skipped, len(orphans), orphan_kinds))
