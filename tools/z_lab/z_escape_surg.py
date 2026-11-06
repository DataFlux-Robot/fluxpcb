# -*- coding: utf-8 -*-
"""z_escape_surg — 靶2b 逃逸路由器: pad 走廊 BFS -> 撞同网铜岛即收.

复合算子的路由核(无损版, 不推挤 —— 口袋墙多为固定铜, 绕行即可):
  1. 实时 pairs 取开路对 -> 焊盘端为起点
  2. GridModel 全信号层栅格(固定铜/焊盘/铺铜已膨胀)
  3. A* 多源(start pad 自有铜格)多目标(同网全部自有铜格=岛链),
     8 邻 + 层跳(via_cell_ok), via 惩罚 8 格
  4. 提交: pad 中心 stub + 栅格折线 + 层跳 via(rg.add_track/add_via)
  5. 验: rg.pair_connected + kstate 连通计数差(假闭合盲区教训)

用法: z_escape_surg.py BOARD_DIR NET
判据: opens_delta == expect 才算 closed(与 z_seg_harvest 同口径).
"""
import heapq
import json
import sys
import time
from pathlib import Path

KG = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter/fluxpcb/kicadgen')
sys.path.insert(0, str(KG))
import pcbnew                                # noqa: E402
import routegrid as rg                       # noqa: E402

board_dir = Path(sys.argv[1])
net_name = sys.argv[2]
pcb = sorted(board_dir.glob('*.kicad_pcb'))[0]

sys.path.insert(0, 'E:/DATA/vscode/ZCODE/flux-neurotransmitter')
from fluxpcb.routing import find_kicad       # noqa: E402
from fluxpcb.kernel import state as kstate   # noqa: E402

kicad_cli, kipy = find_kicad()
q = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=True)
mine = [p for p in ((q.detail or {}).get('pairs') or [])
        if p.split('|')[0] == net_name]
if not mine:
    print(json.dumps({'net': net_name, 'closed': True, 'why': 'no live pair'}))
    sys.exit(0)
pa = mine[0].split('|')
A = (float(pa[1]), float(pa[2]))
B = (float(pa[4]), float(pa[5]))

b = pcbnew.LoadBoard(str(pcb))
b.BuildConnectivity()
nc = b.GetNetInfo().GetNetItem(net_name).GetNetCode()
net = b.FindNet(net_name)
G = rg.GRID
lids = rg.signal_layer_ids(b)
gm = rg.GridModel(b, lids)
NX, NY = gm.nx, gm.ny
W = 0.15                       # 信号线宽(挤缝时 clear_exact 会判0.1 也过)

# ── 起点: 焊盘端; 目标: 同网全部自有铜格(岛链) ──────────────
def pad_near(x, y):
    best, bd = None, 1e9
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() != nc:
                continue
            q_ = pad.GetPosition()
            d = ((q_.x / 1e6 - x) ** 2 + (q_.y / 1e6 - y) ** 2) ** 0.5
            if d < bd:
                best, bd = pad, d
    return best


def cells_of_pad(pad, r=0.12):
    cx, cy, hw, hh, _ = rg.pad_rect(pad)
    out = set()
    rr = max(hw, hh) + r
    for gx in range(int((cx - rr) / G), int((cx + rr) / G) + 1):
        for gy in range(int((cy - rr) / G), int((cy + rr) / G) + 1):
            for li in range(gm.nl):
                s = gm.own_cells.get((li, gx, gy))
                if s and nc in s:
                    out.add((li, gx, gy))
    return out


sp = pad_near(*A)
starts = cells_of_pad(sp)
_p = sp.GetPosition()
_pcx, _pcy = _p.x / 1e6, _p.y / 1e6
goals = set()
for (li, gx, gy), ncs in gm.own_cells.items():
    if nc not in ncs or (li, gx, gy) in starts:
        continue
    # 排除起点焊盘自身的膨胀铜环(假目标教训: A* 两格'到达'还在焊盘上)
    _x, _y = (gx + 0.5) * G, (gy + 0.5) * G
    if ((_x - _pcx) ** 2 + (_y - _pcy) ** 2) ** 0.5 > 1.0:
        goals.add((li, gx, gy))
if not starts or not goals:
    print(json.dumps({'net': net_name, 'closed': False,
                      'why': 'no start/goal cells (%d/%d)'
                             % (len(starts), len(goals))}))
    sys.exit(1)

# ── A*(fixed_free 绕行, 层跳 via) ─────────────────────────
def free(li, gx, gy):
    if gx < 1 or gy < 1 or gx >= NX - 1 or gy >= NY - 1:
        return False
    return gm.fixed_free(li, gx, gy, nc) or ((li, gx, gy) in goals)


INF = 1e18
gscore = {}
parent = {}
heap = []
count = 0
for s in starts:
    gscore[s] = 0.0
    heapq.heappush(heap, (0.0, count, s))
    count += 1
found = None
VIA_COST = 8.0
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
        if not free(lay, gx2, gy2):
            continue
        nk = (lay, gx2, gy2)
        step = G * (1.4142 if dx and dy else 1.0)
        if gscore.get(nk, INF) > cur + step:
            gscore[nk] = cur + step
            parent[nk] = node
            heapq.heappush(heap, (cur + step, count, nk))
            count += 1
    for li2 in range(gm.nl):
        if li2 == lay:
            continue
        if gm.via_cell_ok(li2, gx, gy) and (
                gm.fixed_free(li2, gx, gy, nc)
                or (li2, gx, gy) in goals):
            nk = (li2, gx, gy)
            if gscore.get(nk, INF) > cur + VIA_COST * G:
                gscore[nk] = cur + VIA_COST * G
                parent[nk] = node
                heapq.heappush(heap, (cur + VIA_COST * G, count, nk))
                count += 1
    if count > 600000:
        break

if not found:
    print(json.dumps({'net': net_name, 'closed': False,
                      'why': 'no escape path', 'explored': count}))
    sys.exit(1)

chain = []
node = found
while node is not None:
    chain.append(node)
    node = parent.get(node)
chain.reverse()

# ── 提交: stub + 折线 + 层跳 via ──────────────────────────
items = []
pad_pt = sp.GetPosition()
px0, py0 = pad_pt.x / 1e6, pad_pt.y / 1e6
lid_of = {i: lid for i, lid in enumerate(lids)}
prev = None
for (li, gx, gy) in chain:
    x, y = (gx + 0.5) * G, (gy + 0.5) * G
    if prev is None:
        # pad 中心 -> 首格 stub
        if abs(x - px0) > 0.01 or abs(y - py0) > 0.01:
            if gm.clear_exact(px0, py0, x, y, lid_of[li], nc, W):
                items.append(rg.add_track(b, px0, py0, x, y,
                                          lid_of[li], net, W))
            else:
                items.append(rg.add_track(b, px0, py0, x, y,
                                          lid_of[li], net, 0.1))
    else:
        pli, px, py = prev
        if pli != li:
            if not gm.own_via_at(int(px / G), int(py / G), nc):
                if gm.via_exact_ok(px, py, nc):
                    items.append(rg.add_via(b, px, py, net))
        else:
            if gm.clear_exact(px, py, x, y, lid_of[li], nc, W):
                items.append(rg.add_track(b, px, py, x, y,
                                          lid_of[li], net, W))
            else:
                items.append(rg.add_track(b, px, py, x, y,
                                          lid_of[li], net, 0.1))
    prev = (li, x, y)

b.Save(str(pcb))
ok_pair = rg.pair_connected(b, {'nc': nc, 'a': (px0, py0, 0),
                                'b': (B[0], B[1], 0)})
q2 = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=True)
delta = (q.value - q2.value) if (q.value >= 0 and q2.value >= 0) else -99
print(json.dumps({'net': net_name, 'cells': len(chain), 'items': len(items),
                  'via_jumps': sum(1 for i in range(1, len(chain))
                                   if chain[i][0] != chain[i-1][0]),
                  'pair_connected': bool(ok_pair),
                  'opens_delta': delta, 'expect': len(mine),
                  'closed': bool(ok_pair) and delta == len(mine)}))
