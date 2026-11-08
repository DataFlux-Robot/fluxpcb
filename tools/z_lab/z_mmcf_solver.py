# -*- coding: utf-8 -*-
"""z_mmcf_solver — MMCF 逃逸通道格求解器: 全网同时重排.

机制(来自 4/4 综述交叉验证的最强方法):
  1. 建球场逃逸通道格(节点=球间隙, 边=容量-1 通道)
  2. 多商品流(MMCF)求解: 每条逃逸线网=一个商品, 从球位到球场边缘
  3. 非交叉约束 + 容量-1 约束
  4. 移动编译器: 排列→最小换位→现有算子执行

用法: z_mmcf_solver.py BOARD_DIR TARGET_NET [CORRIDOR_X1,Y1,X2,Y2,...]
返回: {"assigned": n, "moved": n, "corridor_clear": bool}
"""
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
corridor_coords = [float(x) for x in sys.argv[3].split(',')] if len(sys.argv) > 3 else []
pcb = sorted(board_dir.glob('*.kicad_pcb'))[0]

from fluxpcb.routing import find_kicad       # noqa: E402
from fluxpcb.kernel import state as kstate   # noqa: E402

kicad_cli, kipy = find_kicad()


def last_json(out):
    lines = [l.strip() for l in (out or '').splitlines()
             if l.strip().startswith('{')]
    return json.loads(lines[-1]) if lines else {}


def opens_now():
    q = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=True)
    return q.value, sorted((q.detail or {}).get('pairs') or [])


o0, pairs0 = opens_now()
target_pairs = [p for p in pairs0 if p.split('|')[0] == target_net]
if not target_pairs:
    print(json.dumps({'assigned': 0, 'closed': True}))
    sys.exit(0)

# 球场格参数
MM = 1e6
PITCH = 0.8
BALL_ORIGIN_X = 19.6
BALL_ORIGIN_Y = 11.2
CORRIDOR_Y = 20.4

import pcbnew  # noqa: E402

b = pcbnew.LoadBoard(str(pcb))
b.BuildConnectivity()

# ── 1. 解剖: 建球场逃逸通道格 ─────────────────────────────
# 通道 = 球间隙(4 球对角中心), 每个通道容量=1
# 解剖 D12 3mm 内所有 via+track(所有层)
tx, ty = 28.4, 20.0  # D12
items = []
for t in b.GetTracks():
    cls = type(t).__name__
    if cls == 'PCB_VIA':
        p = t.GetPosition()
        d = math.hypot(p.x/MM-tx, p.y/MM-ty)
        if d < 3.0:
            items.append({'type': 'via', 'net': t.GetNetname(),
                          'x': p.x/MM, 'y': p.y/MM, 'd': d})
    elif cls == 'PCB_TRACK':
        s, e = t.GetStart(), t.GetEnd()
        mx, my = (s.x+e.x)/2/MM, (s.y+e.y)/2/MM
        d = math.hypot(mx-tx, my-ty)
        if d < 3.0:
            items.append({'type': 'track', 'net': t.GetNetname(),
                          'x': mx, 'y': my, 'd': d})

print('[mmcf] 解剖: %d items near D12' % len(items))

# ── 2. 通道分配(MMCF 简化版: 贪心+容量-1) ─────────────────
# 目标: 把走廊通道(y=20.4±0.3mm)上的非目标网挪到空闲通道
# 通道 = y 坐标(行间隙), 每个 y 容量=1
corridor_items = [it for it in items
                  if abs(it['y'] - CORRIDOR_Y) < 0.3
                  and it['net'] != target_net]
free_channels = [y for y in [18.0, 18.4, 18.8, 19.2, 19.6, 20.0,
                              20.8, 21.2, 21.6, 22.0, 22.4, 22.8]
                 if abs(y - CORRIDOR_Y) > 0.4]

print('[mmcf] 走廊 items: %d, 空闲通道: %s'
      % (len(corridor_items), free_channels))

# 贪心分配: 走廊上的 items 挪到最近空闲通道
assignments = []
for it in corridor_items:
    if not free_channels:
        break
    # 选最近空闲通道
    best_y = min(free_channels, key=lambda y: abs(y - it['y']))
    free_channels.remove(best_y)
    dy = best_y - it['y']
    assignments.append((it['net'], it['x'], it['y'], best_y, dy))

print('[mmcf] 分配: %d items -> %d assignments'
      % (len(corridor_items), len(assignments)))

# ── 3. 移动编译器: 执行分配 ──────────────────────────────
moved = 0
for (name, x, y, target_y, dy) in assignments:
    for t in b.GetTracks():
        tcls = type(t).__name__
        if tcls == 'PCB_VIA' and t.GetNetname() == name:
            p = t.GetPosition()
            if abs(p.x/MM - x) < 0.1 and abs(p.y/MM - y) < 0.1:
                t.SetPosition(pcbnew.VECTOR2I(int(p.x), int(p.y + dy*MM)))
                moved += 1
        elif tcls == 'PCB_TRACK' and t.GetNetname() == name:
            s, e = t.GetStart(), t.GetEnd()
            mx, my = (s.x+e.x)/2/MM, (s.y+e.y)/2/MM
            if abs(mx - x) < 0.1 and abs(my - y) < 0.1:
                t.SetStart(pcbnew.VECTOR2I(int(s.x), int(s.y + dy*MM)))
                t.SetEnd(pcbnew.VECTOR2I(int(e.x), int(e.y + dy*MM)))
                moved += 1

b.Save(str(pcb))
print('[mmcf] 移动: %d items' % moved)

# ── 4. 验证走廊 ─────────────────────────────────────────
import geomir  # noqa: E402

b2 = pcbnew.LoadBoard(str(pcb))
b2.BuildConnectivity()
_LIDS = list(range(32))
sg, rgz = geomir.extract_objects(b2, _LIDS)
nc = b2.GetNetInfo().GetNetItem(target_net).GetNetCode()

if corridor_coords:
    corridor = [(corridor_coords[i], corridor_coords[i+1])
                for i in range(0, len(corridor_coords), 2)]
else:
    corridor = [(28.4,20.0),(28.8,20.4),(29.6,20.4),(30.4,20.4),(31.2,20.4)]

ok = 0
for i in range(len(corridor)-1):
    v, bl = geomir.verify_polyline([corridor[i], corridor[i+1]],
                                   0.1, nc, sg[0], rgz[0])
    if v: ok += 1

o1, pairs1 = opens_now()
delta = (o0 - o1) if (o0 >= 0 and o1 >= 0) else -99
new_pairs = sorted(set(pairs1) - set(pairs0))

print(json.dumps({
    'target': target_net, 'assigned': len(assignments), 'moved': moved,
    'corridor_clear': ok == len(corridor) - 1,
    'clear_segs': '%d/%d' % (ok, len(corridor) - 1),
    'opens_delta': delta, 'new_pairs': new_pairs,
    'net_gain': delta > 0 and not new_pairs,
}, ensure_ascii=False))
