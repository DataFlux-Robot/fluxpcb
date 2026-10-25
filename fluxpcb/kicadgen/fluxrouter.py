# -*- coding: utf-8 -*-
"""fluxrouter — joint multi-layer PathFinder negotiated-congestion router.

Research lineage (K14-K16):
  - PathFinder cost (1+h)*(1+pfac*occ), McMurchie & Ebeling FPGA'95
  - Partial rip-up: only conflicted nets get reripped (Zha & Li FPGA'22;
    K14 full rip-up: pass1 28/60 -> pass2 0/32)
  - Adaptive step: 2-cell skips in uncongested areas (MegaRoute DATE'25)
  - Z2 VTR/VPR convergence schedule: iter1 pfac=0 (pure shortest path),
    then 0.5 x1.3/iter capped — the 1995 x1.6 doubling oscillates;
    per-iteration net shuffle kills order sensitivity (Zha & Li FPGA'22)
  - Z2 all tunables from knowhow_constraints.json (route_constraints)
  - Z4 joint 3D negotiation over ALL stackup signal layers: vias are
    first-class nodes with occupancy/history (TPR ASP-DAC'05, multi-die
    VTR FPL'26, RWRoute ICCAD'21); through-vias cost the same on any
    layer hop (one drill is one drill) but must clear via_block on every
    crossed layer; soft preferred-direction bias on inner layers (UG583)
  - Z5 why_history persisted per iteration + congestion SVG heatmap
    (PathFinder's history field IS the congestion map)

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
import route_constraints as _RC  # noqa: E402

CONS = _RC.load()

PCB = sys.argv[1]
CLS = sys.argv[2] if len(sys.argv) > 2 else ""
MAX_ITERS = int(sys.argv[3]) if len(sys.argv) > 3 else \
    CONS["negotiation"]["max_iterations"]

T0 = time.time()
board = pcbnew.LoadBoard(PCB)
mm = rg.MM

try:
    NET_CLASS = json.load(open(CLS)).get("classes", {})
except (OSError, ValueError):
    NET_CLASS = {}
CLS_W = dict(CONS["class_weights"])
WIDTHS = {"critical": CONS["track_widths"]["critical_mm"],
          "normal": CONS["track_widths"]["normal_mm"],
          "power": CONS["track_widths"]["power_mm"]}
NEG = CONS["negotiation"]
VIA_COST_BASE = float(CONS["via"]["cost"])


def net_w(name):
    return WIDTHS.get(NET_CLASS.get(name, "normal"), 0.2)


def net_cls_w(name):
    return CLS_W.get(NET_CLASS.get(name, "normal"), 1.0)


# signal layers actually routable on this board (Z4): [F, In1, In4, B]
# on the 6-layer fc stackup, [F, B] on 2-layer boards
LIDS = rg.signal_layer_ids(board, CONS)
NL = len(LIDS)
LP = CONS["layer_prefs"]

# per-net per-layer cost multipliers (Y2 generalized to N layers)
LAYER_PREF = {}    # net name -> [mult per router layer index]


def assign_layer_prefs(nets):
    for name, info in nets.items():
        cls = NET_CLASS.get(name, "normal")
        max_d = max(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                    for a, b in info["chains"]) if info["chains"] else 0
        pref = [1.0] * NL
        if cls == "critical":
            pref[0] = LP["critical"]["F"]
            for i in range(1, NL):
                pref[i] = LP["critical"]["B"]
        elif max_d > LP["long_signal_gt_mm"]:
            pref[0] = LP["long_signal"]["F"]
            pref[NL - 1] = LP["long_signal"]["B"]
            if NL >= 3:
                pref[NL - 2] = LP["long_signal"]["B"]
        LAYER_PREF[name] = pref


# soft preferred-direction bias on inner layers (UG583: orthogonal
# inner-layer directions) — (li, moves_x, moves_y) -> multiplier
DIR_MULT = {}
_pd = CONS.get("stackup_6l", {}).get("preferred_direction", {})
for _name, _dir in _pd.items():
    if _name == "F.Cu":
        _lid = pcbnew.F_Cu
    elif _name == "B.Cu":
        _lid = pcbnew.B_Cu
    elif _name.startswith("In") and _name.endswith(".Cu"):
        _lid = getattr(pcbnew, "In%s_Cu" % _name[2:-3], None)
    else:
        _lid = None
    if _lid is None:
        continue
    try:
        li = LIDS.index(_lid)
    except ValueError:
        continue
    if _dir == "horizontal":
        DIR_MULT[(li, True, False)] = 0.85
        DIR_MULT[(li, False, True)] = 1.15
    else:
        DIR_MULT[(li, True, False)] = 1.15
        DIR_MULT[(li, False, True)] = 0.85


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
        lay = NL - 1 if (pad.IsOnLayer(pcbnew.B_Cu) and
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
try:
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
except Exception:
    pass
gm = rg.GridModel(board, layer_ids=LIDS)
NX, NY = gm.nx, gm.ny
NN = NX * NY

h_hist = [__import__("numpy").zeros((NY, NX), dtype="float32")
          for _ in range(NL)]
cell_owner = {}
PFAC = float(NEG["pfac_initial"])

FAST = None


def build_fast():
    global FAST
    cost = []
    for li in range(NL):
        cost.extend(h_hist[li].flatten().tolist())
    occ = b"".join(bytes(gm.occ[li]) for li in range(NL))
    own = {}
    for (li, gx, gy), ncs in gm.own_cells.items():
        i = li * NN + gy * NX + gx
        for nc in ncs:
            own.setdefault(nc, set()).add(i)
    # through-via legality is layer-pair independent: any layer hop drills
    # through everything, so a cell hosts a via only if NO modeled layer
    # blocks it and the hole is clear
    viaany = bytearray(gm.hole_block)
    for li in range(NL):
        viaany = bytearray(x | y for x, y in zip(viaany,
                                                 gm.via_block[li]))
    owner = [0] * (NL * NN)
    for (li, gx, gy), nc in cell_owner.items():
        owner[li * NN + gy * NX + gx] = nc
    FAST = {"cost": cost, "occ": occ, "own": own, "owner": owner,
            "viaany": viaany}


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
_cur_net_name = ""
_cur_pref = None


def astar_negotiate(a, b, nc, cls_weight, net_name=""):
    global _cur_net_name, _cur_pref
    _cur_net_name = net_name
    _cur_pref = LAYER_PREF.get(net_name) or [1.0] * NL
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
                for li in range(NL):
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
    via_any = F["viaany"]
    PFM = 1.0 + PFAC
    hpush = heapq.heappush
    hpop = heapq.heappop
    G = rg.GRID
    INF_ = 1e18
    VIA_COST = VIA_COST_BASE * cls_weight

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

        pref_l = _cur_pref[lay]
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
            dm = DIR_MULT.get((lay, dx != 0, dy != 0), 1.0)
            if abs(dx) == 1 and abs(dy) == 1:
                iax = base + gy2 * NX + gx
                iay = base + gy * NX + gx2
                if (occ_l[iax] and iax not in own_set):
                    continue
                if (occ_l[iay] and iay not in own_set):
                    continue
                step = G * 1.4142 * c * cls_weight * pref_l * dm
            else:
                mult = max(abs(dx), abs(dy))
                step = G * mult * c * cls_weight * pref_l * dm
            nk = (lay, gx2, gy2)
            if gscore.get(nk, INF_) > cur + step:
                gscore[nk] = cur + step
                parent[nk] = node
                count += 1
                hx = abs(gx2 - tgx) + abs(gy2 - tgy)
                hpush(heap, (cur + step + 2.0 * hx * G, count, nk))

        # layer transitions (Z4): through-via to any other signal layer
        ib = gy * NX + gx
        if not via_any[ib]:
            for lj in range(NL):
                if lj == lay:
                    continue
                i2 = lj * NN + ib
                if occ_l[i2] and i2 not in own_set:
                    continue
                nk = (lj, gx, gy)
                # a drill is a drill: every layer hop is one through-via
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
    lid = lambda li: LIDS[li]

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


# ---------------------------------------------------------------- heatmap
def write_congestion_svg(path, failed_nets):
    """Z5: history-cost field as an SVG congestion heatmap (the history
    field IS the congestion map — no ML needed for failure attribution).
    One horizontal strip per layer; failed-net anchors overlaid."""
    w_px = min(NX, 400)
    scale = w_px / NX
    h_px = max(2, int(NY * scale))
    padpx = 24
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" '
             f'width="{w_px + padpx}" height="{(h_px + padpx) * NL}" '
             f'font-family="monospace" font-size="10">']
    for li in range(NL):
        y0 = li * (h_px + padpx)
        try:
            lname = board.GetLayerName(LIDS[li])
        except (AttributeError, TypeError):
            lname = f"L{li}"
        parts.append(f'<text x="2" y="{y0 + 12}">{lname}</text>')
        arr = h_hist[li]
        mx = float(arr.max()) if arr.size else 1.0
        if mx <= 0:
            mx = 1.0
        for gy in range(0, NY, 2):
            for gx in range(0, NX, 2):
                v = float(arr[gy, gx])
                if v <= 0.01:
                    continue
                t = min(1.0, v / mx)
                # blue -> red
                r = int(40 + 215 * t)
                b = int(255 - 215 * t)
                g = int(80 * (1 - t))
                parts.append(f'<rect x="{padpx / 2 + gx * scale:.1f}" '
                             f'y="{y0 + padpx / 2 + gy * scale:.1f}" '
                             f'width="{2 * scale:.2f}" '
                             f'height="{2 * scale:.2f}" '
                             f'fill="rgb({r},{g},{b})" opacity="0.75"/>')
        parts.append(f'<rect x="{padpx / 2}" y="{y0 + padpx / 2}" '
                     f'width="{w_px}" height="{h_px}" fill="none" '
                     f'stroke="#666"/>')
    for name in failed_nets[:20]:
        net = board.FindNet(name)
        if net is None:
            continue
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetCode() != net.GetNetCode():
                    continue
                q = pad.GetPosition()
                gx = (q.x / mm - gm.edge[0]) / rg.GRID * scale + padpx / 2
                gy = (q.y / mm - gm.edge[1]) / rg.GRID * scale + padpx / 2
                parts.append(f'<circle cx="{gx:.1f}" cy="{gy:.1f}" '
                             f'r="2.5" fill="none" stroke="#0a0" '
                             f'stroke-width="1.2"/>')
    parts.append("</svg>")
    _P(path).write_text("\n".join(parts), encoding="utf-8")


# ---------------------------------------------------------------- main
def main():
    global PFAC
    random.seed(42)
    nets = audit_nets()
    assign_layer_prefs(nets)
    names = list(nets.keys())
    for n in names:
        nets[n]["conflicted"] = True   # first iteration: rip everything
    # Z2 VTR schedule: iteration 1 routes pure shortest-path (pfac=0),
    # iteration 2 starts congestion pressure at pfac_initial, then
    # geometric growth capped at pfac_max
    PFAC = float(NEG["pfac_first_iter"])
    H_INC = float(NEG["h_increment"])
    PHYS_PEN = float(NEG["physical_fail_penalty"])
    results = []
    iters_used = 0
    why = {}
    why_history = []

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
        # Z2: per-iteration shuffle — Zha&Li FPGA'22: net-order sensitivity
        # is the dominant quality-variance source (up to 96% CPD swing)
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
                path = astar_negotiate(a, b, nc, cw, name)
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
                        h_hist[li_][y0:y1, x0:x1] += PHYS_PEN
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

        why_history.append({"iter": it, **why,
                            "routed": sum(1 for n in names
                                          if nets[n].get("items")),
                            "overuse": overuse})
        if overuse == 0 and unroutable == 0:
            break
        # stagnation break: unchanged routed/overuse across 4 iterations
        # after the pfac ramp has engaged (it>=6) means the negotiation
        # has plateaued — burn no more multi-layer A* budget (fc: ~3min/
        # iter at 4 layers). Earlier windows would cut the ramp short.
        if it >= 6 and len(why_history) >= 4:
            last4 = why_history[-4:]
            if all(h["routed"] == last4[0]["routed"]
                   and h["overuse"] == last4[0]["overuse"]
                   for h in last4):
                break
        for (li, gx, gy), _ns in over:
            h_hist[li][gy, gx] += H_INC
        # Z2 VTR schedule handoff: after the shortest-path iteration,
        # engage congestion at pfac_initial, then grow geometrically
        if it == 1:
            PFAC = float(NEG["pfac_initial"])
        else:
            PFAC = min(PFAC * float(NEG["pfac_growth"]),
                       float(NEG["pfac_max"]))
        if it == MAX_ITERS:
            break

    # NEVER leave cross-net overlaps on the final board: negotiation
    # allows temporary overlap and resolves it by rip-up, but a net still
    # conflicted at loop exit would save its route OVER another net's
    # copper — and Z1 then fix-marks that short as converged copper,
    # making Freerouting defend it (fc: 150 shorts, 3V3 micro-segments
    # crossing UART lines on F.Cu). Conflicted nets forfeit their routes.
    for name in names:
        if nets[name].get("conflicted") and nets[name].get("items"):
            for item in nets[name]["items"]:
                try:
                    board.Remove(item)
                except Exception:
                    pass
            release_cells(nets[name].get("cells", []), nets[name]["nc"])
            nets[name]["items"] = []
            nets[name]["cells"] = []

    try:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    except Exception:
        pass
    board.BuildConnectivity()
    final = board.GetConnectivity().GetUnconnectedCount(True)
    board.Save(PCB)
    fragmented = {n: len(nets[n]["chains"]) for n in names
                  if not nets[n].get("items") and nets[n]["chains"]}
    svg_path = ""
    try:
        svg_path = str(_P(PCB).with_suffix(".congestion.svg"))
        write_congestion_svg(svg_path, sorted(fragmented))
    except Exception:
        pass
    print(json.dumps({
        "router": "pathfinder", "iters": iters_used, "layers": NL,
        "why_last": why, "why_history": why_history,
        "nets": len(names),
        "routed_nets": sum(1 for n in names if nets[n].get("items")),
        "final": final, "failed_nets": sorted(fragmented)[:12],
        "svg": svg_path, "zone_stats": gm.zone_stats,
        "time_s": round(time.time() - T0, 1)}, ensure_ascii=False))


main()
