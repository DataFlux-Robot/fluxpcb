# -*- coding: utf-8 -*-
"""fluxrouter — PathFinder negotiated-congestion router with partial
rip-up (Zha & Li) and adaptive step size (MegaRoute-inspired).

Improvements over K14 (all research-backed):
  - Partial rip-up: only conflicted nets get reripped each iteration,
    successful routes persist (K14 full rip-up: pass1 28/60 -> pass2 0/32)
  - Adaptive step: 2-cell skips in uncongested areas (cost < 1.5),
    1-cell precision near obstacles — halves node expansion in open regions
  - All-segment exact verification + 0.30 grid margin + clear_exact w/2

Runs inside KiCad Python:  fluxrouter.py PCB CLSFILE [MAX_ITERS]
Prints one JSON line (route_session-compatible superset).
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
MAX_ITERS = int(sys.argv[3]) if len(sys.argv) > 3 else 40

T0 = time.time()
board = pcbnew.LoadBoard(PCB)
mm = rg.MM

try:
    NET_CLASS = json.load(open(CLS)).get("classes", {})
except (OSError, ValueError):
    NET_CLASS = {}
CLS_W = {"critical": 0.8, "normal": 1.0, "power": 1.3}
WIDTHS = {"critical": 0.15, "normal": 0.2, "power": 0.4}


def net_w(name):
    return WIDTHS.get(NET_CLASS.get(name, "normal"), 0.2)


def net_cls_w(name):
    return CLS_W.get(NET_CLASS.get(name, "normal"), 1.0)


# ---------------------------------------------------------------- audit
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


# ---------------------------------------------------------------- model
gm = rg.GridModel(board)
NX, NY = gm.nx, gm.ny
NN = NX * NY

h_hist = [__import__("numpy").zeros((NY, NX), dtype="float32")
          for _ in range(2)]
cell_owner = {}
PFAC = 0.5

FAST = None


def build_fast():
    global FAST
    cost = []
    for li in (0, 1):
        cost.extend(h_hist[li].flatten().tolist())
    occ = bytes(gm.occ[0]) + bytes(gm.occ[1])
    own = {}
    for (li, gx, gy), ncs in gm.own_cells.items():
        i = li * NN + gy * NX + gx
        for nc in ncs:
            own.setdefault(nc, set()).add(i)
    viaok = (bytes(gm.via_block[0]) + bytes(gm.via_block[1])
             + bytes(gm.hole_block))
    owner = [0] * (2 * NN)
    for (li, gx, gy), nc in cell_owner.items():
        owner[li * NN + gy * NX + gx] = nc
    FAST = {"cost": cost, "occ": occ, "own": own, "owner": owner,
            "viaok": viaok}


def claim_cells(cells, nc):
    for (li, gx, gy) in cells:
        FAST["owner"][li * NN + gy * NX + gx] = nc
        cell_owner[(li, gx, gy)] = nc


def release_cells(cells, nc):
    for (li, gx, gy) in cells:
        if cell_owner.get((li, gx, gy)) == nc:
            FAST["owner"][li * NN + gy * NX + gx] = 0
            cell_owner.pop((li, gx, gy), None)


# ---------------------------------------------------------------- A*
def astar_negotiate(a, b, nc, cls_weight):
    x1, y1, l1 = a
    x2, y2, l2 = b

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
        return None

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
        return None
    tgx, tgy = int(x2 / rg.GRID), int(y2 / rg.GRID)

    F = FAST
    cost_l = F["cost"]
    occ_l = F["occ"]
    owner_l = F["owner"]
    own_set = F["own"].get(nc, frozenset())
    via_l = F["viaok"]
    PFM = 1.0 + PFAC
    hpush = heapq.heappush
    hpop = heapq.heappop
    G = rg.GRID
    INF_ = 1e18
    VIA_COST = 8.0 * cls_weight

    sx, sy = start[1], start[2]
    heap = [(0.0, 0, start)]
    gscore = {start: 0.0}
    parent = {}
    count = 0
    found = None
    while heap:
        _, _, node = hpop(heap)
        if node in goals:
            found = node
            break
        lay, gx, gy = node
        base = lay * NN
        cur = gscore[node]

        # ADAPTIVE STEP (MegaRoute-inspired): 2-cell skips in open areas
        moves = ((1, 0), (-1, 0), (0, 1), (0, -1),
                 (1, 1), (1, -1), (-1, 1), (-1, -1))
        if cost_l[base + gy * NX + gx] < 1.5 and count % 8 == 0:
            moves = moves + ((2, 0), (0, 2))

        for dx, dy in moves:
            gx2, gy2 = gx + dx, gy + dy
            if gx2 < 1 or gy2 < 1 or gx2 >= NX - 1 or gy2 >= NY - 1:
                continue
            i2 = base + gy2 * NX + gx2
            if occ_l[i2] and i2 not in own_set:
                continue
            if abs(dx) > 1 or abs(dy) > 1:
                im = base + (gy + dy // 2) * NX + (gx + dx // 2)
                if occ_l[im] and im not in own_set:
                    continue
            ow = owner_l[i2]
            c = cost_l[i2] * (PFM if ow and ow != nc else 1.0)
            if abs(dx) == 1 and abs(dy) == 1:
                iax = base + gy2 * NX + gx
                iay = base + gy * NX + gx2
                if (occ_l[iax] and iax not in own_set):
                    continue
                if (occ_l[iay] and iay not in own_set):
                    continue
                step = G * 1.4142 * c * cls_weight
            else:
                mult = max(abs(dx), abs(dy))
                step = G * mult * c * cls_weight
            nk = (lay, gx2, gy2)
            if gscore.get(nk, INF_) > cur + step:
                gscore[nk] = cur + step
                parent[nk] = node
                count += 1
                hx = abs(gx2 - tgx) + abs(gy2 - tgy)
                hpush(heap, (cur + step + 2.0 * hx * G, count, nk))

        nk = (1 - lay, gx, gy)
        ib = gy * NX + gx
        i_via = (1 - lay) * NN + ib
        if (not via_l[i_via] and not via_l[lay * NN + ib]
                and not via_l[2 * NN + ib]
                and (not occ_l[i_via] or i_via in own_set)):
            if gscore.get(nk, INF_) > cur + VIA_COST:
                gscore[nk] = cur + VIA_COST
                parent[nk] = node
                count += 1
                hpush(heap, (cur + VIA_COST, count, nk))
        if count > 600000:
            return None
    if not found:
        return None
    chain = []
    node = found
    while node and node != start:
        chain.append(node)
        node = parent.get(node)
    chain.append(start)
    chain.reverse()
    return chain


# ---------------------------------------------------------------- commit
def cells_to_items(path, nc, name, a, b):
    net = board.FindNet(name)
    if net is None:
        return None, None, None
    w = net_w(name)
    items, cells = [], []
    pts = [(li, (gx + 0.5) * rg.GRID, (gy + 0.5) * rg.GRID)
           for (li, gx, gy) in path]
    lid = lambda li: pcbnew.F_Cu if li == 0 else pcbnew.B_Cu

    # start/end stubs
    fx, fy = pts[0][1], pts[0][2]
    lx_, ly_ = pts[-1][1], pts[-1][2]
    ax, ay, al = a
    bx, by, bl = b
    if abs(fx - ax) >= 0.01 or abs(fy - ay) >= 0.01:
        if not gm.clear_exact(ax, ay, fx, fy, lid(al), nc, w):
            return None, None, (al, int(ax / rg.GRID), int(ay / rg.GRID))
        items.append(rg.add_track(board, ax, ay, fx, fy, lid(al), net, w))
    if abs(lx_ - bx) >= 0.01 or abs(ly_ - by) >= 0.01:
        if not gm.clear_exact(lx_, ly_, bx, by, lid(pts[-1][0]), nc, w):
            for i in items:
                try:
                    board.Remove(i)
                except Exception:
                    pass
            return None, None, (pts[-1][0], int(bx / rg.GRID),
                                int(by / rg.GRID))
        items.append(rg.add_track(board, lx_, ly_, bx, by,
                                  lid(pts[-1][0]), net, w))

    prev = None
    for (li, x, y) in pts:
        if prev is not None:
            pli, px, py = prev
            if pli != li:
                if not gm.own_via_at(int(px / rg.GRID), int(py / rg.GRID),
                                     nc):
                    if not gm.via_exact_ok(px, py, nc):
                        for i in items:
                            try:
                                board.Remove(i)
                            except Exception:
                                pass
                        return None, None, (pli, int(px / rg.GRID),
                                            int(py / rg.GRID))
                    items.append(rg.add_via(board, px, py, net))
            else:
                if not gm.clear_exact(px, py, x, y, lid(li), nc, w):
                    for i in items:
                        try:
                            board.Remove(i)
                        except Exception:
                            pass
                    return None, None, (li, int(px / rg.GRID),
                                        int(py / rg.GRID))
                items.append(rg.add_track(board, px, py, x, y, lid(li),
                                          net, w))
            cells.append((pli, int(px / rg.GRID), int(py / rg.GRID)))
        prev = (li, x, y)
    if prev is not None:
        cells.append((prev[0], int(prev[1] / rg.GRID),
                      int(prev[2] / rg.GRID)))
    return [i for i in items if i is not None], cells, None


# ---------------------------------------------------------------- main
def main():
    global PFAC
    random.seed(42)
    try:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    except Exception:
        pass
    nets = audit_nets()
    names = list(nets.keys())
    for n in names:
        nets[n]["conflicted"] = True   # first iteration: rip everything
    PFAC = 0.5
    H_INC = 0.4
    results = []
    iters_used = 0
    why = {}

    for it in range(1, MAX_ITERS + 1):
        iters_used = it
        # PARTIAL RIP-UP: only conflicted or unrouted nets get reripped
        for name in names:
            info = nets[name]
            if info.get("conflicted", True):
                for item in info.get("items", []):
                    try:
                        board.Remove(item)
                    except Exception:
                        pass
                release_cells(info.get("cells", []), info["nc"])
                info["items"] = []
                info["cells"] = []

        build_fast()
        order = [n for n in names
                 if nets[n].get("conflicted", True)
                 or not nets[n].get("items")]
        random.shuffle(order)
        unroutable = 0
        why = {"nopath": 0, "physical": 0, "verify": 0}

        for name in order:
            info = nets[name]
            nc = info["nc"]
            cw = net_cls_w(name)
            all_cells = []
            ok = True
            for (a, b) in info["chains"]:
                path = astar_negotiate(a, b, nc, cw)
                if path is None:
                    why["nopath"] += 1
                    ok = False
                    break
                items, cells, fail_cell = cells_to_items(path, nc, name,
                                                         a, b)
                if items is None:
                    why["physical"] += 1
                    if fail_cell is not None:
                        li_, gx_, gy_ = fail_cell
                        y0 = max(0, gy_ - 2)
                        y1 = min(h_hist[li_].shape[0], gy_ + 3)
                        x0 = max(0, gx_ - 2)
                        x1 = min(h_hist[li_].shape[1], gx_ + 3)
                        h_hist[li_][y0:y1, x0:x1] += 5.0
                    ok = False
                    break
                if not rg.pair_connected(board, {"nc": nc, "a": a, "b": b}):
                    for i in items:
                        try:
                            board.Remove(i)
                        except Exception:
                            pass
                    why["verify"] += 1
                    ok = False
                    break
                info.setdefault("items", []).extend(items)
                claim_cells(cells, nc)
                all_cells.extend(cells)

            if not ok:
                for item in info.get("items", []):
                    try:
                        board.Remove(item)
                    except Exception:
                        pass
                for c in all_cells:
                    if cell_owner.get(c) == nc:
                        release_cells([c], nc)
                info["items"] = []
                info["cells"] = []
                unroutable += 1
            else:
                info["cells"] = all_cells

        # overuse + conflict marking for next iteration's partial rip-up
        claims = {}
        for c, nc in cell_owner.items():
            claims.setdefault(c, set()).add(nc)
        over = [(c, ns) for c, ns in claims.items() if len(ns) > 1]
        overuse = len(over)
        conflicted_nets = set()
        for c, ns in over:
            conflicted_nets.update(ns)
        for name in names:
            nets[name]["conflicted"] = (name in conflicted_nets
                                        or not nets[name].get("items"))

        if overuse == 0 and unroutable == 0:
            break
        for (li, gx, gy), _ns in over:
            h_hist[li][gy, gx] += H_INC
        PFAC *= 1.6 if it < 10 else 1.15
        if it == MAX_ITERS:
            break

    try:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    except Exception:
        pass
    board.BuildConnectivity()
    final = board.GetConnectivity().GetUnconnectedCount(True)
    board.Save(PCB)
    fragmented = {n: len(nets[n]["chains"]) for n in names
                  if not nets[n].get("items") and nets[n]["chains"]}
    print(json.dumps({
        "router": "pathfinder", "iters": iters_used,
        "why_last": why,
        "nets": len(names),
        "routed_nets": sum(1 for n in names if nets[n].get("items")),
        "final": final, "failed_nets": sorted(fragmented)[:12],
        "time_s": round(time.time() - T0, 1)}, ensure_ascii=False))


main()
