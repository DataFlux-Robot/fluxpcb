# -*- coding: utf-8 -*-
"""Simplified push-and-shove on the routegrid lattice.

When a path crosses a cell occupied by another net's router-placed
route, instead of paying the congestion penalty (avoiding), this engine
DISPLACES the blocking track perpendicular to the new route by one or
more grid cells, then checks that the displaced track still passes
clearance. If yes, both commit; if the displaced track hits another,
cascade (recursion depth 3, Freerouting's ShoveTraceAlgo approach).

This is Plan B (KiCad's native PNS not exposed via SWIG; PCBWorld's
custom C++ binding requires GitHub which is unreachable). The grid
model makes shove simpler than Freerouting's shape-based approach:
tracks are cell-paths, shoving = moving cell-paths sideways.

Runs inside KiCad Python:  pns_router.py PCB CLSFILE
Prints one JSON line: {"shoves":n,"routed":n,"final":ratsnest,...}
"""
import heapq
import json
import random
import sys
import time
from pathlib import Path as _P

import pcbnew

sys.path.insert(0, str(_P(__file__).resolve().parent))
import routegrid as rg          # noqa: E402

PCB = sys.argv[1]
CLS = sys.argv[2] if len(sys.argv) > 2 else ""
T0 = time.time()
board = pcbnew.LoadBoard(PCB)
mm = rg.MM

try:
    NET_CLASS = json.load(open(CLS)).get("classes", {})
except (OSError, ValueError):
    NET_CLASS = {}
WIDTHS = {"critical": 0.15, "normal": 0.2, "power": 0.4}


def net_w(name):
    return WIDTHS.get(NET_CLASS.get(name, "normal"), 0.2)


# reuse fluxrouter's audit (same file, module-level script)
def audit_nets():
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

    def pad_anchor(pad):
        lay = 1 if (pad.IsOnLayer(pcbnew.B_Cu) and
                    not pad.IsOnLayer(pcbnew.F_Cu)) else 0
        q = pad.GetPosition()
        return (q.x / mm, q.y / mm, lay)

    nets = {}
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
        chains = []
        anchors_by_isl = [[pad_anchor(p) for p in isl]
                          for isl in islands.values()]
        inside = {0}
        while len(inside) < len(islands):
            best = None
            for ia in inside:
                for ib in range(len(islands)):
                    if ib in inside:
                        continue
                    for pa in anchors_by_isl[ia]:
                        for pb in anchors_by_isl[ib]:
                            d2 = (pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2
                            if best is None or d2 < best[0]:
                                best = (d2, ia, ib, pa, pb)
            _, ia, ib, pa, pb = best
            chains.append((pa, pb))
            inside.add(ib)
        nets[pads[0].GetNetname()] = {"nc": nc, "chains": chains}
    return nets


gm = rg.GridModel(board)
NX, NY = gm.nx, gm.ny
NN = NX * NY

# Pad-only occupancy (hard block — pads cannot move)
occ_pads = bytearray(2 * NN)
for fp in board.GetFootprints():
    for pad in fp.Pads():
        try:
            cx, cy, hw, hh, _ang = rg.pad_rect(pad)
            ext = max(hw, hh)
            for li in (0, 1):
                if not pad.IsOnLayer(pcbnew.F_Cu if li == 0 else pcbnew.B_Cu):
                    continue
                for gx in range(int((cx - ext - 0.30) / rg.GRID),
                               int((cx + ext + 0.30) / rg.GRID) + 1):
                    for gy in range(int((cy - ext - 0.30) / rg.GRID),
                                   int((cy + ext + 0.30) / rg.GRID) + 1):
                        cx2 = (gx + 0.5) * rg.GRID
                        cy2 = (gy + 0.5) * rg.GRID
                        if rg.pt_seg_dist(cx2, cy2, cx - hw, cy,
                                          cx + hw, cy) <= ext + 0.30:
                            occ_pads[li * NN + gy * NX + gx] = 1
        except AttributeError:
            continue


# ---------------------------------------------------------------- shove
def find_blocking_track(cell, nc):
    """The track item whose path contains `cell` and belongs to a
    different net. Returns (item, netcode) or None."""
    li, gx, gy = cell
    cx, cy = (gx + 0.5) * rg.GRID, (gy + 0.5) * rg.GRID
    best, bd = None, 1.0
    for it in board.GetTracks():
        try:
            if it.Type() == pcbnew.PCB_VIA_T:
                q = it.GetPosition()
                d = ((q.x / mm - cx) ** 2 + (q.y / mm - cy) ** 2) ** 0.5
                if d < 0.4 and it.GetNetCode() != nc:
                    if d < bd:
                        best, bd = it, d
            else:
                if it.GetLayer() != (pcbnew.F_Cu if li == 0 else pcbnew.B_Cu):
                    continue
                if it.GetNetCode() == nc:
                    continue
                s, e = it.GetStart(), it.GetEnd()
                d = rg.pt_seg_dist(cx, cy, s.x / mm, s.y / mm,
                                   e.x / mm, e.y / mm)
                if d < 0.3 and d < bd:
                    best, bd = it, d
        except AttributeError:
            continue
    return best


def shove_track(item, direction, nc_shoving, depth=0):
    """LOCAL-BUMP shove: only displace the portion of `item` within 2mm
    of its intersection with the new route, creating a bump. Endpoints
    stay connected (Freerouting's ShoveTraceAlgo approach simplified
    for the grid model). Falls back to full translation if the track
    is shorter than 4mm."""
    if depth > 3 or item is None:
        return False
    dx, dy = [(rg.GRID, 0), (-rg.GRID, 0), (0, rg.GRID), (0, -rg.GRID)][direction]

    if item.Type() == pcbnew.PCB_VIA_T:
        # vias: simple translation (they're point-like)
        q = item.GetPosition()
        nx, ny = q.x / mm + dx, q.y / mm + dy
        if not gm.via_exact_ok(nx, ny, item.GetNetCode()):
            return False
        item.SetPosition(pcbnew.VECTOR2I(int(nx * mm), int(ny * mm)))
        return True

    # PCB_TRACK: three-segment bump if long enough
    s, e = item.GetStart(), item.GetEnd()
    sx, sy = s.x / mm, s.y / mm
    ex, ey = e.x / mm, e.y / mm
    length = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
    nc = item.GetNetCode()
    lid = item.GetLayer()
    w = rg.track_w(item) * 2

    if length < 2.0:
        # short track: translate whole thing
        nsx, nsy = sx + dx, sy + dy
        nex, ney = ex + dx, ey + dy
        if not gm.clear_exact(nsx, nsy, nex, ney, lid, nc, w):
            return False
        item.SetStart(pcbnew.VECTOR2I(int(nsx * mm), int(nsy * mm)))
        item.SetEnd(pcbnew.VECTOR2I(int(nex * mm), int(ney * mm)))
        return True

    # long track: create a bump — split into 3 segments
    # [start -> bump_start] [bump (displaced)] [bump_end -> end]
    # bump covers 40% of the track centered on the midpoint
    mid_x, mid_y = (sx + ex) / 2, (sy + ey) / 2
    bump_len = min(length * 0.4, 2.0)

    # unit vector along the track
    ux, uy = (ex - sx) / length, (ey - sy) / length
    # bump start/end points (on original track line)
    bx1, by1 = mid_x - ux * bump_len / 2, mid_y - uy * bump_len / 2
    bx2, by2 = mid_x + ux * bump_len / 2, mid_y + uy * bump_len / 2
    # displaced bump endpoints
    dx1, dy1 = bx1 + dx, by1 + dy
    dx2, dy2 = bx2 + dx, by2 + dy

    # check the three segments at their new positions
    # seg1: start -> bump_start_displaced
    if not gm.clear_exact(sx, sy, dx1, dy1, lid, nc, w):
        return False
    # seg2: bump (displaced)
    if not gm.clear_exact(dx1, dy1, dx2, dy2, lid, nc, w):
        return False
    # seg3: bump_end_displaced -> end
    if not gm.clear_exact(dx2, dy2, ex, ey, lid, nc, w):
        return False

    # cascade: check if the displaced bump hits other tracks
    for it2 in board.GetTracks():
        if it2 is item:
            continue
        try:
            if it2.GetNetCode() == nc:
                continue
            if it2.GetLayer() != lid:
                continue
            s2, e2 = it2.GetStart(), it2.GetEnd()
            if rg.seg_seg_dist((dx1, dy1), (dx2, dy2),
                               (s2.x / mm, s2.y / mm),
                               (e2.x / mm, e2.y / mm)) < 0.35:
                if not shove_track(it2, direction, nc_shoving, depth + 1):
                    return False
        except AttributeError:
            continue

    # commit the bump: shrink original, add two new segments
    # original becomes: start -> bump_start -> bump_end -> end
    # (we keep the original item as start->bump_start, add bump + tail)
    item.SetEnd(pcbnew.VECTOR2I(int(bx1 * mm), int(by1 * mm)))
    rg.add_track(board, bx1, by1, dx1, dy1, lid, board.FindNet(
        item.GetNetname() or ""), w)   # jog out
    rg.add_track(board, dx1, dy1, dx2, dy2, lid, board.FindNet(
        item.GetNetname() or ""), w)   # displaced run
    rg.add_track(board, dx2, dy2, bx2, by2, lid, board.FindNet(
        item.GetNetname() or ""), w)   # jog back
    rg.add_track(board, bx2, by2, ex, ey, lid, board.FindNet(
        item.GetNetname() or ""), w)   # tail
    return True


# ---------------------------------------------------------------- route
def route_with_shove(a, b, nc, name):
    """Route a pair, shoving aside any blocking router-placed tracks."""
    x1, y1, l1 = a
    x2, y2, l2 = b
    net = board.FindNet(name)
    if net is None:
        return False
    w = net_w(name)

    def near_cell(x, y, lay):
        gx, gy = int(x / rg.GRID), int(y / rg.GRID)
        for r in range(0, 6):
            for dx in range(-r, r + 1):
                for dy in (range(-r, r + 1) if abs(dx) == r else (0,)):
                    if gm.fixed_free(lay, gx + dx, gy + dy, nc):
                        return (lay, gx + dx, gy + dy)
        return None

    start = near_cell(x1, y1, l1)
    if start is None:
        return False

    def anchor_pad(x, y):
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

    goals = set()
    tp = anchor_pad(x2, y2)
    if tp is not None:
        cx, cy, hw, hh, _ang = rg.pad_rect(tp)
        r = max(hw, hh) + 0.1
        for gx in range(int((cx - r) / rg.GRID),
                        int((cx + r) / rg.GRID) + 1):
            for gy in range(int((cy - r) / rg.GRID),
                            int((cy + r) / rg.GRID) + 1):
                for li in (0, 1):
                    s = gm.own_cells.get((li, gx, gy))
                    if s and nc in s:
                        goals.add((li, gx, gy))
    if not goals:
        return False

    # A* allowing shove-through: when a cell is occupied by another
    # router-placed net, we can either pay the cost (like fluxrouter) or
    # SHOVE the blocker. Here we route through and handle shoves at commit.
    tgx, tgy = int(x2 / rg.GRID), int(y2 / rg.GRID)
    # A* blocks only on PADS; track cells are passable (shove at commit)
    occ = bytes(occ_pads)
    own = {}
    for (li, gx, gy), ncs in gm.own_cells.items():
        i = li * NN + gy * NX + gx
        for n in ncs:
            own.setdefault(n, set()).add(i)

    G = rg.GRID
    INF_ = 1e18
    sx, sy = start[1], start[2]
    heap = [(0.0, 0, start)]
    gscore = {start: 0.0}
    parent = {}
    count = 0
    found = None
    VIA_COST = 8.0
    while heap:
        _, _, node = heapq.heappop(heap)
        if node in goals:
            found = node
            break
        lay, gx, gy = node
        base = lay * NX * NY
        cur = gscore[node]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            gx2, gy2 = gx + dx, gy + dy
            if gx2 < 1 or gy2 < 1 or gx2 >= NX - 1 or gy2 >= NY - 1:
                continue
            i2 = base + gy2 * NX + gx2
            if occ[i2] and i2 not in own.get(nc, frozenset()):
                continue
            step = G * (1.4142 if dx and dy else 1.0)
            nk = (lay, gx2, gy2)
            if gscore.get(nk, INF_) > cur + step:
                gscore[nk] = cur + step
                parent[nk] = node
                count += 1
                hx = abs(gx2 - tgx) + abs(gy2 - tgy)
                heapq.heappush(heap, (cur + step + 2.0 * hx * G, count, nk))
        nk = (1 - lay, gx, gy)
        if gm.via_cell_ok(1 - lay, gx, gy) and \
                gm.fixed_free(1 - lay, gx, gy, nc) and \
                gscore.get(nk, INF_) > cur + VIA_COST:
            gscore[nk] = cur + VIA_COST
            parent[nk] = node
            count += 1
            heapq.heappush(heap, (cur + VIA_COST, count, nk))
        if count > 400000:
            return False
    if not found:
        return False
    chain = []
    node = found
    while node and node != start:
        chain.append(node)
        node = parent.get(node)
    chain.append(start)
    chain.reverse()

    # commit with shove: for each cell that crosses another net's track,
    # try to shove it aside
    shoves = 0
    items = []
    lid = lambda li: pcbnew.F_Cu if li == 0 else pcbnew.B_Cu
    pts = [(li, (gx + 0.5) * G, (gy + 0.5) * G) for li, gx, gy in chain]

    # stubs
    fx, fy = pts[0][1], pts[0][2]
    lx_, ly_ = pts[-1][1], pts[-1][2]
    if abs(fx - x1) >= 0.01 or abs(fy - y1) >= 0.01:
        if gm.clear_exact(x1, y1, fx, fy, lid(pts[0][0]), nc, w):
            items.append(rg.add_track(board, x1, y1, fx, fy,
                                      lid(pts[0][0]), net, w))
    if abs(lx_ - x2) >= 0.01 or abs(ly_ - y2) >= 0.01:
        if gm.clear_exact(lx_, ly_, x2, y2, lid(pts[-1][0]), nc, w):
            items.append(rg.add_track(board, lx_, ly_, x2, y2,
                                      lid(pts[-1][0]), net, w))

    prev = None
    for (li, x, y) in pts:
        if prev is not None:
            pli, px, py = prev
            if pli != li:
                if not gm.own_via_at(int(px / G), int(py / G), nc):
                    if gm.via_exact_ok(px, py, nc):
                        items.append(rg.add_via(board, px, py, net))
            else:
                # check clearance; if fails, try shove the blocker
                if not gm.clear_exact(px, py, x, y, lid(li), nc, w):
                    blocker = find_blocking_track((li, int(px / G), int(py / G)), nc)
                    if blocker is not None:
                        # determine shove direction: perpendicular to new route
                        if abs(x - px) > abs(y - py):  # horizontal route
                            dirs = (2, 3)  # shove vertically
                        else:
                            dirs = (0, 1)  # shove horizontally
                        shoved = False
                        for d in dirs:
                            if shove_track(blocker, d, nc):
                                shoved = True
                                shoves += 1
                                break
                        if not shoved:
                            for i in items:
                                try:
                                    board.Remove(i)
                                except Exception:
                                    pass
                            return False
                items.append(rg.add_track(board, px, py, x, y, lid(li),
                                          net, w))
        prev = (li, x, y)

    # island verification
    if rg.pair_connected(board, {"nc": nc, "a": a, "b": b}):
        return True
    for i in items:
        try:
            board.Remove(i)
        except Exception:
            pass
    return False


# ---------------------------------------------------------------- main
def main():
    random.seed(42)
    try:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    except Exception:
        pass
    nets = audit_nets()
    names = sorted(nets.keys(), key=lambda n: len(nets[n]["chains"]))
    routed = 0
    shoves_total = 0
    for name in names:
        info = nets[name]
        nc = info["nc"]
        all_ok = True
        for (a, b) in info["chains"]:
            if not route_with_shove(a, b, nc, name):
                all_ok = False
                break
        if all_ok:
            routed += 1
    board.BuildConnectivity()
    final = board.GetConnectivity().GetUnconnectedCount(True)
    board.Save(PCB)
    print(json.dumps({"router": "shove", "nets": len(names),
                      "routed": routed, "final": final,
                      "time_s": round(time.time() - T0, 1)},
                     ensure_ascii=False))


main()
