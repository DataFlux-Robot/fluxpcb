# -*- coding: utf-8 -*-
"""fluxrouter — PathFinder negotiated-congestion router (McMurchie &
Ebeling 1995) on the shared routegrid lattice.

The replacement for one-shot greedy sweeps + blind rip-up: every
iteration ALL negotiable nets are rerouted on a cost field

    cost(cell) = (1 + h[cell]) * (1 + p_fac * max(0, overuse(cell,net)))

foreign ROUTER-placed copper is EXPENSIVE, not blocked — competing nets
negotiate: history h grows on contested cells each iteration, pushing
first-comers aside (this is also the pocket-opener: the tracks walling
sealed anchors become negotiable and get pushed away).

Fixed copper (pads, dog-bone fanout, committed session tracks) stays
hard-blocked in occ; same-net passability per routegrid semantics.

Runs inside KiCad Python:  fluxrouter.py PCB CLSFILE [MAX_ITERS]
Prints one JSON line (route_session-compatible superset):
  {"router":"pathfinder","iters":k,"converged":bool,"overuse":n,
   "committed":n,"final":ratsnest,"failed_nets":[...],"time_s":t}
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
    """{name: {"nc", "chains": [((x,y,l),(x,y,l))...]}} — per net, the
    MST chain of cross-island anchor pairs (Prim, same as conn_audit)."""
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
INF = 1e9

# PathFinder cost fields (numpy for the negotiation arithmetic)
import numpy as np
h_hist = [np.zeros((NY, NX), dtype=np.float32) for _ in range(2)]
occ_cnt = [np.zeros((NY, NX), dtype=np.int16) for _ in range(2)]
# cell -> netcode currently routed through it (dict for ownership)
cell_owner = {}


def cost_at(li, gx, gy, nc):
    """(1 + h) * (1 + pfac * foreign_occupancy)."""
    i = gy * NX + gx
    if not gm.fixed_free(li, gx, gy, nc):
        return None
    own = cell_owner.get((li, gx, gy))
    foreign = 0
    if own is not None and own != nc:
        foreign = 1
    c = (1.0 + float(h_hist[li][gy, gx])) * (1.0 + PFAC * foreign)
    return c


def astar_negotiate(a, b, nc, cls_weight):
    """Weighted A* over the negotiated cost field. Returns cell path
    [(li,gx,gy)...] or None. Foreign router copper is costly but
    traversable; fixed copper blocks (own passable)."""
    x1, y1, l1 = a
    x2, y2, l2 = b
    # start: nearest grid cell free for this net near anchor a
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
    # goals: THE TARGET PAD's own-copper cells (K11 lesson: free cells
    # near the pad end the path in mid-air — no electrical contact; and
    # dangling same-net fragments must not count as arrival either)
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
        for li in (0, 1):
            for gx in range(int((cx - r) / rg.GRID),
                            int((cx + r) / rg.GRID) + 1):
                for gy in range(int((cy - r) / rg.GRID),
                                int((cy + r) / rg.GRID) + 1):
                    s = gm.own_cells.get((li, gx, gy))
                    if s and nc in s:
                        goals.add((li, gx, gy))
    if not goals:
        return None
    tgx, tgy = int(x2 / rg.GRID), int(y2 / rg.GRID)
    sx, sy = start[1], start[2]
    heap = [(0.0, 0, start)]
    gscore = {start: 0.0}
    parent = {}
    VIA_COST = 8.0 * cls_weight
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
            c = cost_at(lay, gx2, gy2, nc)
            if c is None:
                continue
            if dx and dy and (
                    cost_at(lay, gx + dx, gy, nc) is None or
                    cost_at(lay, gx, gy + dy, nc) is None):
                continue          # no diagonal through blocked corners
            step = rg.GRID * (1.4142 if dx and dy else 1.0) * c * cls_weight
            nk = (lay, gx2, gy2)
            if gscore.get(nk, 1e18) > cur + step:
                gscore[nk] = cur + step
                parent[nk] = node
                count += 1
                hx = abs(gx2 - tgx) + abs(gy2 - tgy)
                heapq.heappush(heap, (cur + step + 2.0 * hx * rg.GRID, count,
                                      nk))
        nk = (1 - lay, gx, gy)
        if gm.via_cell_ok(1 - lay, gx, gy) and \
                gm.fixed_free(1 - lay, gx, gy, nc) and \
                gscore.get(nk, 1e18) > cur + VIA_COST:
            gscore[nk] = cur + VIA_COST
            parent[nk] = node
            count += 1
            heapq.heappush(heap, (cur + VIA_COST, count, nk))
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


def cells_to_items(path, nc, name):
    """Per-step emission with exact verification; returns (items, cells,
    None) or (None, None, fail_cell) on violation — the caller raises h at
    the failing cell so later iterations route around it (PathFinder's
    violation-as-congestion feedback)."""
    net = board.FindNet(name)
    if net is None:
        return None, None, None
    w = net_w(name)
    items, cells = [], []
    pts = [(li, (gx + 0.5) * rg.GRID, (gy + 0.5) * rg.GRID)
           for (li, gx, gy) in path]
    lid = lambda li: pcbnew.F_Cu if li == 0 else pcbnew.B_Cu
    prev = None
    for k, (li, x, y) in enumerate(pts):
        if prev is not None:
            pli, px, py = prev
            if pli != li:
                # layer switch: via at the shared cell
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
                if abs(x - px) >= 0.01 and abs(y - py) >= 0.01:  # diagonal
                    if not gm.clear_exact(px, py, x, y, lid(li), nc, w):
                        import sys as _s
                        lii = 0 if lid(li) == pcbnew.F_Cu else 1
                        for net2, a2, b2, ex2 in gm.segs[lii]:
                            if net2 == nc:
                                continue
                            if rg.seg_seg_dist((px, py), (x, y), a2, b2) < rg.CLEAR_MM + ex2 + max(0.0, w / 2 - 0.1):
                                print(f"      seg blocker net={net2} {a2}->{b2}", file=_s.stderr)
                        for i in items:      # orphaned partials polluted
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
    # zone fill for accurate islands (fresh process = no wrapper poison)
    try:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    except Exception:
        pass
    nets = audit_nets()
    names = list(nets.keys())
    PFAC = 0.5
    H_INC = 0.4
    failed = {n: 0 for n in names}
    no_path = set()
    best = None            # (overuse, {name: (items, cells, chains)})
    iters_used = 0

    for it in range(1, MAX_ITERS + 1):
        iters_used = it
        # rip all router-placed items, reset occupancy
        for name in names:
            info = nets[name]
            for it_key in ("items",):
                for item in info.get(it_key, []):
                    try:
                        board.Remove(item)
                    except Exception:
                        pass
            info["items"] = []
            info["cells"] = []
        cell_owner.clear()

        order = list(names)
        random.shuffle(order)
        unroutable = 0
        why = {"nopath": 0, "physical": 0}
        for name in order:
            if name in no_path:
                continue
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
                items, cells, fail_cell = cells_to_items(path, nc, name)
                if items is None:
                    why["physical"] += 1
                    if fail_cell is not None:
                        li_, gx_, gy_ = fail_cell
                        y0 = max(0, gy_ - 2); y1 = min(h_hist[li_].shape[0], gy_ + 3)
                        x0 = max(0, gx_ - 2); x1 = min(h_hist[li_].shape[1], gx_ + 3)
                        h_hist[li_][y0:y1, x0:x1] += 5.0
                    ok = False
                    break
                # success: register occupancy + KEEP the items for the
                # next iteration's rip-up (the missing bookkeeping made
                # routed_nets read 0 and duplicates pile up)
                # island verification (the honest gate): a grid-success
                # whose copper doesn't close the pair is rolled back
                if not rg.pair_connected(board, {"nc": nc, "a": a, "b": b}):
                    for i in items:
                        try:
                            board.Remove(i)
                        except Exception:
                            pass
                    why["verify"] = why.get("verify", 0) + 1
                    ok = False
                    break
                info.setdefault("items", []).extend(items)
                for c in cells:
                    cell_owner[c] = nc
                all_cells.extend(cells)
            if not ok:
                # rollback this net's partial items
                for item in info.get("items", []):
                    try:
                        board.Remove(item)
                    except Exception:
                        pass
                # remove its cells from ownership
                for c in all_cells:
                    if cell_owner.get(c) == nc:
                        del cell_owner[c]
                info["items"] = []
                info["cells"] = []
                unroutable += 1
            else:
                info["cells"] = all_cells

        # overuse = cells claimed by >1 net
        claims = {}
        for c, nc in cell_owner.items():
            claims.setdefault(c, set()).add(nc)
        over = [(c, ns) for c, ns in claims.items() if len(ns) > 1]
        overuse = len(over)
        if overuse == 0 and unroutable == 0:
            break
        # negotiation updates: history grows on contested cells
        for (li, gx, gy), _ns in over:
            h_hist[li][gy, gx] += H_INC
        PFAC *= 1.6 if it < 10 else 1.15
        if it == MAX_ITERS:
            break

    # converged or budget out — items are already on the board; save
    try:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    except Exception:
        pass
    board.BuildConnectivity()
    final = board.GetConnectivity().GetUnconnectedCount(True)
    board.Save(PCB)
    fragmented = {}
    for name in names:
        info = nets[name]
        if not info.get("items") and info["chains"]:
            fragmented[name] = len(info["chains"])
    print(json.dumps({
        "router": "pathfinder", "iters": iters_used, "why_last": why,
        "converged": overuse == 0, "overuse": overuse,
        "nets": len(names), "routed_nets": sum(
            1 for n in names if nets[n].get("items")),
        "final": final, "failed_nets": sorted(fragmented)[:12],
        "time_s": round(time.time() - T0, 1)}, ensure_ascii=False))


PFAC = 0.5
main()
