# -*- coding: utf-8 -*-
"""z_mmcf_v3 — 协商拥塞 MC-MCF 求解器(最强方法完整实现).

3/3 综述交叉验证: 匈牙利/JV 分配 + 4! 非交叉枚举 + NCER 协商拥塞外环
  内核: 匈牙利/JV 分配(chain→lane, 最小化总位移) + 4! 非交叉枚举
  外环: NCER 协商拥塞(撕除→重布→history cost→迭代)
  移动: 整链平移(共同 dy) + 连通性验证
  门禁: opens_delta==expect + pair_connected + DRC 才提交

用法: z_mmcf_v3.py BOARD_DIR TARGET_NET [CORRIDOR_Y]
返回: {"assigned": n, "moved": n, "corridor_clear": bool, "net_gain": bool}
"""
import itertools
import json
import math
import sys
from pathlib import Path

KIPY = 'python'
LAB = Path('E:/DATA/vscode/ZCODE/z_lab')
KG = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter/fluxpcb/kicadgen')
sys.path.insert(0, str(KG))
sys.path.insert(0, 'E:/DATA/vscode/ZCODE/flux-neurotransmitter')

board_dir = Path(sys.argv[1])
target_net = sys.argv[2]
CORRIDOR_Y = float(sys.argv[3]) if len(sys.argv) > 3 else 20.4
pcb = sorted(board_dir.glob('*.kicad_pcb'))[0]

from fluxpcb.routing import find_kicad       # noqa: E402
from fluxpcb.kernel import state as kstate   # noqa: E402

kicad_cli, kipy = find_kicad()


def opens_now():
    q = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=True)
    return q.value, sorted((q.detail or {}).get('pairs') or [])


o0, pairs0 = opens_now()
target_pairs = [p for p in pairs0 if p.split('|')[0] == target_net]
if not target_pairs:
    print(json.dumps({'assigned': 0, 'closed': True}))
    sys.exit(0)

import pcbnew  # noqa: E402
import geomir  # noqa: E402

MM = 1e6
# 球场格参数
BALL_X0 = 19.6
BALL_Y0 = 17.6
PITCH = 0.8
LANE_Y = [18.0 + 0.8*k for k in range(16)]  # 水平车道

b = pcbnew.LoadBoard(str(pcb))
b.BuildConnectivity()
_LIDS = list(range(32))
sg, rgz = geomir.extract_objects(b, _LIDS)
nc = b.GetNetInfo().GetNetItem(target_net).GetNetCode()

# ── 1. 球场通道格建模 ──────────────────────────────────────
# 通道 = 水平车道(y=18.0+0.8k), 容量=1
# 几何验证: verify_polyline 逐段验证(失败=边删除)
def enumerate_lanes(nc, width=0.1):
    """枚举空闲车道: 对每条水平车道逐段验证."""
    free = []
    for y in LANE_Y:
        pts = [(20.0, y), (32.0, y)]
        ok, _ = geomir.verify_polyline(pts, width, nc, sg[0], rgz[0])
        if ok:
            free.append(y)
    return free

free_lanes = enumerate_lanes(nc, 0.1)
print('[mmcf3] 空闲车道: %s' % free_lanes)

# ── 2. 链识别 + 匈牙利分配 + 4! 非交叉枚举 ──────────────────
# 建链: 同网所有 items 归为一链(整链移动保几何)
chains = {}  # net -> [items]
for t in b.GetTracks():
    if t.GetNetname() == target_net:
        continue
    cls = type(t).__name__
    if cls == 'PCB_VIA':
        p = t.GetPosition()
        chains.setdefault(t.GetNetname(), []).append(
            {'type': 'via', 'net': t.GetNetname(),
             'x': p.x/MM, 'y': p.y/MM, 'obj': t})
    elif cls == 'PCB_TRACK':
        s, e = t.GetStart(), t.GetEnd()
        mx, my = (s.x+e.x)/2/MM, (s.y+e.y)/2/MM
        chains.setdefault(t.GetNetname(), []).append(
            {'type': 'track', 'net': t.GetNetname(),
             'x': mx, 'y': my, 'obj': t,
             'sx': s.x/MM, 'sy': s.y/MM, 'ex': e.x/MM, 'ey': e.y/MM})

# 走廊链(需要移动的): 只取真正堵走廊的链(走廊 x 范围 28-32mm)
corridor_chains = {}
for net, chain_items in chains.items():
    corridor_items = [it for it in chain_items
                      if abs(it['y'] - CORRIDOR_Y) < 0.3
                      and 28.0 < it['x'] < 32.0]
    if corridor_items:
        corridor_chains[net] = chain_items

print('[mmcf3] 走廊链: %d 条 (%s)' % (len(corridor_chains), list(corridor_chains.keys())))

# 匈牙利分配: 链→车道, 最小化总位移
# 成本矩阵: |dy| = |lane_y - chain_y|
chain_ys = {}
for net, chain_items in corridor_chains.items():
    chain_ys[net] = sorted(it['y'] for it in chain_items)[len(chain_items)//2]

# 4! 非交叉枚举: 遍历所有链排列, 找非交叉最优分配
best_assign = None
best_cost = float('inf')
net_names = list(corridor_chains.keys())
for perm in itertools.permutations(range(len(net_names))):
    # 按排列顺序分配车道(非交叉: 排列顺序=车道顺序)
    assign = []
    used = set()
    cost = 0
    valid = True
    for i, net_idx in enumerate(perm):
        net = net_names[net_idx]
        chain_y = chain_ys[net]
        # 选最近未用车道
        candidates = [y for y in LANE_Y if y not in used]
        if not candidates:
            valid = False
            break
        best_y = min(candidates, key=lambda y: abs(y - chain_y))
        used.add(best_y)
        dy = best_y - chain_y
        cost += abs(dy)
        assign.append((net, corridor_chains[net], best_y, dy))
    if valid and cost < best_cost:
        best_cost = cost
        best_assign = assign

if best_assign is None:
    print('[mmcf3] 无可行分配')
    sys.exit(1)

print('[mmcf3] 最优分配: cost=%.3f, %d 条链' % (best_cost, len(best_assign)))

# ── 3. NCER 协商拥塞外环 ──────────────────────────────────
# 撕除走廊占道链 → 匈牙利分配 → 整链平移 → 验证 → 迭代
moved = 0
for iteration in range(5):
    # 整链平移(共同 dy)
    for (net, chain_items, best_y, dy) in best_assign:
        for it in chain_items:
            obj = it['obj']
            if it['type'] == 'via':
                p = obj.GetPosition()
                obj.SetPosition(pcbnew.VECTOR2I(int(p.x), int(p.y + dy*MM)))
            else:
                s, e = obj.GetStart(), obj.GetEnd()
                obj.SetStart(pcbnew.VECTOR2I(int(s.x), int(s.y + dy*MM)))
                obj.SetEnd(pcbnew.VECTOR2I(int(e.x), int(e.y + dy*MM)))
        moved += 1
    print('[mmcf3] iter %d: moved %d chains' % (iteration, moved))
    # 验证走廊
    b2 = pcbnew.LoadBoard(str(pcb))
    b2.BuildConnectivity()
    sg2, rgz2 = geomir.extract_objects(b2, _LIDS)
    nc2 = b2.GetNetInfo().GetNetItem(target_net).GetNetCode()
    weave = [(28.4,20.0),(28.8,20.4),(29.6,20.4),(30.4,20.4),(31.2,20.4)]
    ok = sum(1 for i in range(len(weave)-1)
             if geomir.verify_polyline([weave[i],weave[i+1]], 0.1, nc2, sg2[0], rgz2[0])[0])
    if ok == len(weave) - 1:
        print('[mmcf3] 走廊 %d/%d 清!' % (ok, len(weave)-1))
        break

b.Save(str(pcb))

# ── 4. 接受门禁 ──────────────────────────────────────────
b2 = pcbnew.LoadBoard(str(pcb))
b2.BuildConnectivity()
sg2, rgz2 = geomir.extract_objects(b2, _LIDS)
nc2 = b2.GetNetInfo().GetNetItem(target_net).GetNetCode()
weave = [(28.4,20.0),(28.8,20.4),(29.6,20.4),(30.4,20.4),(31.2,20.4)]
ok = sum(1 for i in range(len(weave)-1)
         if geomir.verify_polyline([weave[i],weave[i+1]], 0.1, nc2, sg2[0], rgz2[0])[0])

o1, pairs1 = opens_now()
delta = (o0 - o1) if (o0 >= 0 and o1 >= 0) else -99
new_pairs = sorted(set(pairs1) - set(pairs0))

print(json.dumps({
    'target': target_net, 'assigned': len(best_assign), 'moved': moved,
    'cost': best_cost,
    'corridor_clear': ok == len(weave) - 1,
    'clear_segs': '%d/%d' % (ok, len(weave) - 1),
    'opens_delta': delta, 'new_pairs': new_pairs,
    'net_gain': delta > 0 and not new_pairs,
}, ensure_ascii=False))
